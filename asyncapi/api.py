import asyncio
import dataclasses
import logging
from typing import Any, Callable, Dict, Tuple, Type

import orjson
from broadcaster import Broadcast
from jsondaora import DeserializationError, asdataclass, dataclass_asjson

from .exceptions import (
    ChannelOperationNotFoundError,
    ChannelPublishNotFoundError,
    InvalidChannelError,
    InvalidMessageError,
    OperationIdNotFoundError,
)
from .specification_v2_0_0 import Operation, Specification


OperationsTypeHint = Dict[Tuple[str, str], Callable[..., Any]]


@dataclasses.dataclass
class AsyncApi:
    spec: Specification
    operations: OperationsTypeHint
    broadcast: Broadcast
    republish_error_messages: bool = True
    logger: logging.Logger = logging.getLogger(__name__)

    async def publish_json(
        self, channel_id: str, message: Dict[str, Any]
    ) -> None:
        await self.broadcast.publish(
            channel=channel_id,
            message=self.parse_message(
                channel_id, self.payload(channel_id, **message)
            ).decode(),
        )

    async def publish(self, channel_id: str, message: Any) -> None:
        await self.broadcast.publish(
            channel=channel_id,
            message=self.parse_message(channel_id, message).decode(),
        )

    async def connect(self) -> None:
        await self.broadcast.connect()

    def payload(self, channel_id: str, **message: Any) -> Any:
        type_ = self.publish_payload_type(channel_id)
        return self.payload_type(type_, channel_id, **message)

    def subscriber_payload(self, channel_id: str, **message: Any) -> Any:
        type_ = self.subscribe_payload_type(channel_id)
        return self.payload_type(type_, channel_id, **message)

    def payload_type(
        self, type_: Type[Any], channel_id: str, **message: Any
    ) -> Any:
        if type_ and dataclasses.is_dataclass(type_):
            return asdataclass(message, type_)

        return message

    async def listen_all(self) -> None:
        tasks = []

        for channel_id in self.spec.channels.keys():
            task = asyncio.create_task(self.listen(channel_id))
            task.add_done_callback(task_callback)
            tasks.append(task)

        await asyncio.gather(*tasks)

    async def listen(self, channel_id: str) -> None:
        if self.spec.channels is None:
            raise InvalidChannelError(channel_id)

        operation_id = self.subscribe_operation(channel_id).operation_id

        if operation_id is None:
            raise ChannelOperationNotFoundError(channel_id)

        async with self.broadcast.subscribe(channel=channel_id) as subscriber:
            async for event in subscriber:
                try:
                    json_message = orjson.loads(event.message)
                    payload = self.subscriber_payload(
                        channel_id, **json_message
                    )

                    coro = self.operations[(channel_id, operation_id)](payload)

                    if asyncio.iscoroutine(coro):
                        await coro

                except (orjson.JSONDecodeError, DeserializationError):
                    raise

                except KeyError:
                    raise OperationIdNotFoundError(operation_id)

                except Exception:
                    if not self.republish_error_messages:
                        raise

                    self.logger.exception(f"message={event.message[:100]}")

                    try:
                        await self.publish(channel_id, payload)
                    except UnboundLocalError:
                        await self.publish_json(channel_id, json_message)

    def publish_operation(self, channel_id: str) -> Operation:
        return self.operation('publish', channel_id)

    def subscribe_operation(self, channel_id: str) -> Operation:
        return self.operation('subscribe', channel_id)

    def operation(self, op_name: str, channel_id: str) -> Operation:
        operation: Operation

        try:
            operation = getattr(self.spec.channels[channel_id], op_name)

            if operation is None:
                raise ChannelPublishNotFoundError(channel_id)

        except KeyError:
            raise InvalidChannelError(channel_id)

        else:
            return operation

    def parse_message(self, channel_id: str, message: Any) -> Any:
        type_ = self.publish_payload_type(channel_id)

        if type_:
            if not isinstance(message, type_):
                raise InvalidMessageError(message, type_)

            return dataclass_asjson(message)

        return message

    def publish_payload_type(self, channel_id: str) -> Any:
        operation = self.publish_operation(channel_id)

        if operation.message is None:
            return None

        return operation.message.payload

    def subscribe_payload_type(self, channel_id: str) -> Any:
        operation = self.subscribe_operation(channel_id)

        if operation.message is None:
            return None

        return operation.message.payload


def task_callback(future: Any) -> None:
    future.result()