#!/bin/sh

mtail --progs mtail/mtail --logtostderr --address :: --port 3903 --logs logs/*.log,logs/progress-trace.csv

