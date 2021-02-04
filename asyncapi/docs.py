import dataclasses
import importlib
import json
import os.path
from enum import Enum
from functools import partial
from http import HTTPStatus
from typing import Any, Dict, Iterable, List, Optional

import jinja2
import typer
import uvicorn
import yaml
from apidaora import (
    ContentType,
    Response,
    RoutedControllerTypeHint,
    appdaora,
    css,
    html,
    javascript,
    route,
)
from apischema.json_schema import serialization_schema

from asyncapi import Operation, Specification
from asyncapi import docs_filters as jinja_filters
from asyncapi.builder import build_spec_from_path
from asyncapi.specification_v2_0_0 import as_camel_case


def main(
    api_module: str = typer.Option('', envvar='ASYNCAPI_MODULE'),
    host: str = typer.Option('0.0.0.0', envvar='ASYNCAPI_HOST'),
    port: int = typer.Option(5000, envvar='ASYNCAPI_PORT'),
    path: Optional[str] = typer.Option(None, envvar='ASYNCAPI_PATH'),
    html_params: Optional[str] = typer.Option(
        None, envvar='ASYNCAPI_HTML_PARAMS'
    ),
) -> None:
    if path:
        spec = build_spec_from_path(path)
    else:
        spec = getattr(importlib.import_module(api_module), 'spec')

    if html_params:
        dict_html_params = {
            str_key_value.split('=')[0]: str_key_value.split('=')[1]
            for str_key_value in html_params.split(';')
        }
    else:
        dict_html_params = {}

    start(spec, host, port, dict_html_params)


def start(
    spec: Specification, host: str, port: int, html_params: Dict[str, str]
) -> None:
    controllers = build_yaml_spec_controllers(spec) + [
        build_json_spec_controller(spec)
    ]
    controllers.extend(build_spec_docs_controllers(spec, html_params))
    app = appdaora(controllers)
    uvicorn.run(app, host=host, port=port)


def build_yaml_spec_controllers(
    spec: Specification,
) -> List[RoutedControllerTypeHint]:
    def controller() -> Response:
        return Response(
            status=HTTPStatus.OK,
            content_type=ContentType.APPLICATION_YAML,
            body=yaml.dump(spec_asjson(spec)),
            headers=(),
        )

    @route.get('/asyncapi.yaml')
    def controller_yaml() -> Response:
        return controller()

    @route.get('/asyncapi.yml')
    def controller_yml() -> Response:
        return controller()

    return [controller_yaml, controller_yml]


def build_json_spec_controller(
    spec: Specification,
) -> RoutedControllerTypeHint:
    @route.get('/asyncapi.json')
    def controller() -> Response:
        return Response(
            status=HTTPStatus.OK,
            content_type=ContentType.APPLICATION_JSON,
            body=json.dumps(spec_asjson(spec), indent=2).encode(),
            headers=(),
        )

    return controller


def spec_asjson(spec: Specification) -> Dict[str, Any]:
    json_spec: Dict[str, Any] = _spec_asjson(spec)
    spec_messages_dict = (
        spec.components.messages
        if spec.components and spec.components.messages
        else {}
    )
    json_spec_messages = {}

    for message_name, message_type in spec_messages_dict.items():
        if message_type.payload:
            json_spec['components']['messages'][message_name][
                'payload'
            ] = serialization_schema(message_type.payload)

            if message_type.name:
                json_spec_messages[message_type.name] = message_name

    for server in json_spec['servers'].values():
        server.pop('name', None)

    for channel_dict, channel in zip(
        json_spec['channels'].values(), spec.channels.values()
    ):
        channel_dict.pop('name', None)

        if 'subscribe' in channel_dict:
            set_operation_message(
                channel_dict['subscribe'],
                channel.subscribe,
                json_spec_messages,
            )

        if 'publish' in channel_dict:
            set_operation_message(
                channel_dict['publish'], channel.publish, json_spec_messages
            )

    return json_spec


def set_operation_message(
    operation_dict: Dict[str, Any],
    operation: Optional[Operation],
    json_spec_messages: Dict[str, str],
) -> None:
    operation_dict['message'].pop('contentType', None)

    if (
        operation
        and operation.message
        and operation.message.name
        and operation.message.name in json_spec_messages
    ):
        operation_dict['message'] = {
            '$ref': (
                '#/components/messages/'
                f'{json_spec_messages[operation.message.name]}'
            )
        }

    elif operation and operation.message and operation.message.payload:
        operation_dict['message']['payload'] = serialization_schema(
            operation.message.payload
        )


def _spec_asjson(generic_value: Any) -> Any:
    json_value: Any

    if dataclasses.is_dataclass(generic_value):
        json_value = {}

        for field in dataclasses.fields(generic_value):
            field_value = _spec_asjson(
                getattr(generic_value, field.name, None)
            )

            if field_value is not None and field_value != '':
                json_value[as_camel_case(field.name)] = field_value

    elif isinstance(generic_value, dict):
        json_value = {k: _spec_asjson(v) for k, v in generic_value.items()}

    elif not isinstance(generic_value, str) and isinstance(
        generic_value, Iterable
    ):
        json_value = [_spec_asjson(v) for v in generic_value]

    elif isinstance(generic_value, Enum):
        json_value = generic_value.value

    else:
        json_value = generic_value

    return json_value


def build_spec_docs_controllers(
    spec: Specification,
    html_params: Dict[str, str],
) -> List[RoutedControllerTypeHint]:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = partial(os.path.join, current_dir, 'docs-template', 'template')
    template_loader = jinja2.FileSystemLoader(
        searchpath=os.path.join(current_dir, 'docs-template')
    )
    template_env = jinja2.Environment(loader=template_loader)
    template_env.filters['containTags'] = jinja_filters.contain_tags
    template_env.filters['containNoTag'] = jinja_filters.contain_no_tag
    template_env.filters['split'] = jinja_filters.split
    template_env.filters['markdown2html'] = jinja_filters.markdown2html
    template_env.filters['isExpandable'] = jinja_filters.is_expandable
    template_env.filters['dump'] = jinja_filters.dump
    template_env.filters[
        'nonParserExtensions'
    ] = jinja_filters.non_parser_extensions
    template_env.filters['isObject'] = jinja_filters.is_object
    template_env.filters['isArray'] = jinja_filters.is_array
    template_env.filters['keys'] = jinja_filters.keys
    template_env.filters['head'] = jinja_filters.head
    template_env.filters[
        'getPayloadExamples'
    ] = jinja_filters.get_payload_examples
    template_env.filters['generateExample'] = jinja_filters.generate_example
    template_env.filters[
        'getHeadersExamples'
    ] = jinja_filters.get_headers_examples
    template_env.filters['boolean'] = jinja_filters.boolean
    json_spec = spec_asjson(spec)
    set_messages(json_spec)
    docs_spec_obj = DocsSpecObject(json_spec)

    @route.get('/docs')
    def index_controller() -> Response:
        template = template_env.get_template('template/index.html')
        return html(
            template.render(params=html_params, asyncapi=docs_spec_obj)
        )

    @route.get('/css/tailwind.min.css')
    def tailwind_controller() -> Response:
        return css(open(file_path('css', 'tailwind.min.css')).read())

    @route.get('/css/atom-one-dark.min.css')
    def atom_one_dark_controller() -> Response:
        return css(open(file_path('css', 'atom-one-dark.min.css')).read())

    @route.get('/css/main.css')
    def main_css_controller() -> Response:
        return css(open(file_path('css', 'main.css')).read())

    @route.get('/js/highlight.min.js')
    def highlight_controller() -> Response:
        return javascript(open(file_path('js', 'highlight.min.js')).read())

    @route.get('/js/main.js')
    def main_js_controller() -> Response:
        return javascript(open(file_path('js', 'main.js')).read())

    return [
        index_controller,
        tailwind_controller,
        atom_one_dark_controller,
        main_css_controller,
        highlight_controller,
        main_js_controller,
    ]


class DocsSpecObject:
    def __init__(self, spec: Dict[str, Any]):
        self.spec = spec

    def __getattr__(self, attr_name: str) -> Any:
        if attr_name == 'ext':
            return lambda ext_name: self.spec.get(ext_name)

        elif attr_name == 'allMessages':
            return lambda: all_messages(self.spec)

        elif attr_name == 'json':
            return tojson(self.spec)

        elif attr_name == 'hasServers':
            return lambda: 'servers' in self.spec

        elif attr_name == 'hasChannels':
            return lambda: 'channels' in self.spec

        elif attr_name == 'hasTags':
            return lambda: 'tags' in self.spec

        elif attr_name == 'hasPublish':
            return lambda: 'publish' in self.spec

        elif attr_name == 'hasSubscribe':
            return lambda: 'subscribe' in self.spec

        elif (
            attr_name == 'properties'
            or attr_name == 'servers'
            or attr_name == 'channels'
        ):
            return lambda: dict(
                **{
                    k: DocsSpecObject(v)
                    for k, v in self.spec.get(attr_name, {}).items()
                }
            ).items()

        elif attr_name == 'tags':
            return lambda: (
                [DocsSpecObject(l_obj) for l_obj in attr]
                if isinstance(attr := self.spec.get(attr_name), list)
                else None
            )

        return lambda: (
            DocsSpecObject(attr)
            if isinstance(attr := self.spec.get(attr_name), dict)
            else attr
        )


def set_messages(spec: Dict[str, Any]) -> None:
    messages_refs = {}

    for muid, message in (
        spec.get('components', {}).get('messages', {}).items()
    ):
        message['uid'] = message.get('name', muid)
        messages_refs[f'#/components/messages/{muid}'] = message

    for channel_name, channel in spec.get('channels', {}).items():
        pub_message = channel.get('publish', {}).get('message', {})
        sub_message = channel.get('subscribe', {}).get('message', {})

        if '$ref' in pub_message:
            if pub_message['$ref'] in messages_refs:
                channel['publish']['message'] = messages_refs[
                    channel['publish']['message']['$ref']
                ]

        if '$ref' in sub_message:
            if sub_message['$ref'] in messages_refs:
                channel['subscribe']['message'] = messages_refs[
                    channel['subscribe']['message']['$ref']
                ]


def all_messages(spec: Any) -> Any:
    messages = {}

    for k, m in spec.get('components', {}).get('messages', {}).items():
        messages[k] = DocsSpecObject(m)

    return messages.items()


def tojson(spec: Any) -> Any:
    def _tojson(attr_name: Optional[str] = None) -> Any:
        if attr_name is None:
            return spec

        return spec.get(attr_name)

    return _tojson


def run() -> None:
    typer.run(main)


if __name__ == '__main__':
    run()
