#!/usr/bin/env python3
# -*- mode: python; python-indent: 4 -*-

import random
import time

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

# Resource can be required for some backends, e.g. Jaeger
# If resource wouldn't be set - traces wouldn't appears in Jaeger
resource = Resource(attributes={
        "service.name": "NSO"
        })

trace.set_tracer_provider(TracerProvider(resource=resource))
tracer = trace.get_tracer(__name__)

otlp_exporter = OTLPSpanExporter(endpoint="http://localhost:4317", insecure=True)

span_processor = BatchSpanProcessor(otlp_exporter)

trace.get_tracer_provider().add_span_processor(span_processor)

with tracer.start_as_current_span("root-span"):
    time.sleep(random.random()*0.5+0.05)
    with tracer.start_as_current_span("shoo"):
        time.sleep(random.random()*0.5+0.05)
        with tracer.start_as_current_span("too"):
            time.sleep(random.random()*0.5+0.05)
            print("Hello world!")
