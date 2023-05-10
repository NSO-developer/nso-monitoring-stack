ifeq "$(NCS_DIR)" ""
$(error NCS_DIR is not setup. Source ncsrc to setup NSO environment before proceeding)
endif

NSO_VERSION = $(shell ncs --version)
NSO_VER_MAJ = $(shell echo $(NSO_VERSION) | cut -f1 -d.)
NSO_VER_MIN = $(shell echo $(NSO_VERSION) | cut -f2 -d. | cut -f1 -d_)
NSO_MAJOR_VERSION = $(NSO_VER_MAJ).$(NSO_VER_MIN)


.PHONY: help
help:
	@echo "NSO major version: $(NSO_MAJOR_VERSION) ($(NSO_VERSION))"
	@echo
	@echo "Makefile rules:"
	@echo " * all              Setup a single node NSO system."
	@echo " * build-pkgs       Build packages."
	@echo " * start            Start environment"
	@echo " * stop             Stop environment"
	@echo " * cli              Start an NSO CLI session"
	@echo " * load-dashboards  Load dashboards into Grafana."


.PHONY: all
all: build-pkgs ncs.conf

.PHONY: build-pkgs
build-pkgs: ncs.conf
	for i in $(shell ls packages); do \
	  if [ -e packages/$${i}/src/Makefile ]; then \
	    echo "#####" Cleaning package: $${i} "#####"; \
	    $(MAKE) -C packages/$${i}/src all || exit 1; \
	  fi \
	done


ncs.conf:
	ncs-setup --dest . --package cisco-ios-cli-3.0


.PHONY: start
start:
	ncs


.PHONY: stop
stop:
	ncs --stop || true


.PHONY: cli
cli:
	ncs_cli -u admin -C


.PHONY: clean
clean:
	for i in $(shell ls packages); do \
	  if [ -e packages/$${i}/src/Makefile ]; then \
	    echo "#####" Cleaning package: $${i} "#####"; \
	    $(MAKE) -C packages/$${i}/src clean || exit 1; \
	  fi \
	done
	rm -rf ncs-cdb/*.cdb state logs scripts target ncs.conf storedstate
	rm -rf README.ncs netsim
	rm -rf packages/cisco-ios-cli-3.0


.PHONY: load-dashboards
load-dashboards:
	# Create folder
	curl -H "Content-Type: application/json" -X POST http://localhost:3000/api/folders --upload grafana/dashboards/folder.json
	./grafana-dashboard.py load --clean -f grafana/dashboards/node-exporter-full.json
	./grafana-dashboard.py load --clean -f grafana/dashboards/nso-log-files.json
	./grafana-dashboard.py load --clean -f grafana/dashboards/nso-metrics-exporter.json
	./grafana-dashboard.py load --clean -f grafana/dashboards/nso-observability-exporter-traces.json
	./grafana-dashboard.py load --clean -f grafana/dashboards/nso-observability-exporter.json
	./grafana-dashboard.py load --clean -f grafana/dashboards/nso-process-exporter.json
	./grafana-dashboard.py load --clean -f grafana/dashboards/nso-prometheus-process-exporter.json
