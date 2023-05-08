# NSO Monitoring Stack

## Overview

This repository is an attempt to make observability and monitoring of NSO
easily available for development, test and production.

**NOTE!** This repository does no contain the installation files for NSO or
the observability-exporter.

![Screenshot](doc/nso-monitoring-stack.png)

## Setup

The stack can be started in any environment capable of running Docker.
NSO still requires Linux or Mac.

**NOTE!** This guide only provide how to run in Linux, as availability of
some components are limited in Mac.

### Dependencies

#### Python modules

The NSO packages and some scripts have dependecies to Python modules not part
of the default Python distribution. They are preferably installed using
virtualenv.

```
❯ python3 -m venv venv
❯ . venv/bin/activate
❯ pip install -r requirements.txt
```

#### Linux dependencies

The Linux related exporters must be installed to get the metrics from
the operating system and log file levels. Example for Debian and Ubuntu.

```
sudo apt install prometheus-node-exporter prometheus-process-exporter mtail
```

### Topology

This guide covers the use-case where NSO is running in the same machine as
the monitoring stack. Mainly due to the fact that the possibility to route of
traffic in/out of a docker environment varies depending on operating system and
version.

The stack the IP address of the computer running the stack must be put in the
*.env* file.
```
PROMETHEUS_VERSION=latest
GRAFANA_VERSION=latest
TEMPO_VERSION=latest
INFLUXDB_VERSION=1.8
HOST=<put the ip here>
```

We wish to find a solution without the need to set the IP address for running
the stack in e.g. your laptop, were the address may change when moving from
work to home or similar.

### NSO

This NSO environment is easily setup using the provided Makefile.

**NOTE!** The NSO dependenices must have been installed: java, ant, ...

Always remember to source the *ncsrc* file for the NSO commands etc.
```
❯ . <path to NSO install directory>/ncsrc
```

Setup the environment and build the packages.
```
❯ . <path to NSO install directory>/ncsrc
❯ make all
...
```

### Observability Exporter

The Observability Export must unfortunately be manully installed as it is no
longer provided as open source software.

Install the package into the *packages* directory.
```
❯ cp <path>/ncs-6.0-observability-exporter-1.1.0.tar.gz packages/.
```

If NSO has not previously been started, the initial configuration of the
progress trace export can copied to the *ncs-cdb* directory:
```
❯ cp observability-exporter/observability-exporter.xml ncs-cdb/.
```

If NSO already has been started once (i.e. the CDB files have been created), you
can load the configuration using the *ncs_load* command:
```
❯ ncs_load -lm observability-exporter/observability-exporter.xml
```

or use the CLI.
```
❯ ncs_cli -Cu admin
admin@ncs# config
admin@ncs(config)# unhide debug
admin@ncs(config)# load merge observability-exporter/observability-exporter.xml
observability-exporter/observability-exporter.xml
admin@ncs(config)# commit
```

## Get it up and runnig

Start NSO
```
❯ ncs
```

Start the monitoring stack
```
❯ docker-compose up -d
```

Load dashboards to Grafana (wait >15 seconds to allow for it to start)
```
❯ make load-dashboards
```

## Start external exporters

This shows how to start the external exporters in separate shells.
How to start them as part of the operating system is currently out of scope of
this guide.

### Prometheus Node Exporter

Exports metrics related to the operating system and hardware levels.
```
sudo prometheus-node-exporter
```

### Prometheus Process Exporter

Useful for exporting process metrics for NSO during packages reloads and during
starts/stops of NSO when the NSO Process Exporter is not running and for other
processes outside NSO.

```
sudo prometheus-process-exporter -config.path process-exporter/config.yaml 
```

### Google mtail

Export metrics from process log files.

```
./run_mtail.sh
```

## Port numbers

This table is a summary of the important TCP ports that are exposed from the
containers or NSO.

| Application | Port |
| prometheus | 9090 | 
| mtail | 3903 | 
| prometheus-node-exporter | 9100 | 
| prometheus-process-exporter | 9256 | 
| nso-metric-exporter | 9098 | 
| nso-process-exporter | 9099 | 
