#!/usr/bin/env bats

export PUBSUB_EMULATOR_HOST=localhost:8086


@test "should publish gcloud-pubsub message http spec" {
    cd docs/src/gcloud_pubsub
    sed -r -e "s/asyncapi-docs/coverage run -p -m 'asyncapi.docs'/" \
        ./docs.sh > /tmp/asyncapi-docs.sh
    bash /tmp/asyncapi-docs.sh > /tmp/asyncapi-docs.log 2>&1 &
    sleep 1
    run coverage run -p publish.py
    sleep 1
    ps ax | grep 'asyncapi.docs' | \
        grep -v -E 'bats|grep' | sed -r -e 's/ ?([0-9]+) .*/\1/g' | \
        xargs kill -SIGTERM 2>/dev/null
    [ "$status" -eq 0 ]
    [ "$output" = "$(cat ../publish.output)" ]
}


@test "should receive gcloud-pubsub message http spec" {
    cd docs/src/gcloud_pubsub
    sed -r -e "s/asyncapi-docs/coverage run -p -m 'asyncapi.docs'/" \
        ./docs.sh > /tmp/asyncapi-docs.sh
    bash /tmp/asyncapi-docs.sh > /tmp/asyncapi-docs.log 2>&1 &
    sleep 1
    sed -r -e "s/asyncapi-subscriber/coverage run -p -m 'asyncapi.subscriber'/" \
        ./subscriber.sh > /tmp/asyncapi-subscriber.sh
    bash /tmp/asyncapi-subscriber.sh > /tmp/asyncapi-subscriber.log &
    sleep 1
    coverage run -p publish.py
    sleep 1
    ps ax | grep -E 'asyncapi.docs|asyncapi.subscriber' | \
        grep -v -E 'bats|grep' | sed -r -e 's/ ?([0-9]+) .*/\1/g' | \
        xargs kill -SIGTERM 2>/dev/null
    [ "$(head -2 /tmp/asyncapi-subscriber.log)" = "$(cat ../subscriber-receive-message.output)" ]
}
