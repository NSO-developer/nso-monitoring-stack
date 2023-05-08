#this file may be included from multiple locations, so need figure out
#where we are
PKGS_DIR=$(shell dirname $(realpath $(lastword $(MAKEFILE_LIST))))

# MY_PY_SRC is only used inside each package, may be overwritten
MY_PY_SRC ?= $(wildcard ../python/*)

PYLINT = pylint --ignore=namespaces --rcfile $(PKGS_DIR)/.pylintrc

# run pylint and check return code
#    Pylint should leave with following status code:
#   * 0 if everything went fine
#   * 1 if some fatal message issued
#   * 2 if some error message issued
#   * 4 if some warning message issued
#   * 8 if some refactor message issued
#   * 16 if some convention message issued
#   * 32 on usage error
#   status 1 to 16 will be bit-ORed so you can know which different
#   categories has been issued by analysing pylint output status code
# we use strict linting, so any type of lint will result in a failure.
pylint:
ifeq ($(SKIP_LINT),true)
	@echo "Skipping $@ because SKIP_LINT=true"
else
	PYLINTHOME=.pylint.d $(PYLINT) --ignore=namespaces $(MY_PY_SRC)
endif


mypy:
ifeq ($(SKIP_LINT),true)
	@echo "Skipping $@ because SKIP_LINT=true"
else
	mypy $(MY_PY_SRC) --check-untyped-defs --ignore-missing-imports
endif

clean: clean-pylint clean-mypy clean-pycache

clean-pylint:
	rm -fr .pylint.d

clean-mypy:
	rm -fr .mypy_cache

clean-pycache:
	- find ../python -name __pycache__ -exec rm -rf {} \;
