ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)

.PHONY: venv vendor clean

venv:
	@bash -c 'source $(ROOT)/integration/scripts/venv.sh'
	@echo ""
	@echo ">> venv is ready at $(ROOT)/.venv"
	@echo ">> To activate it in your current shell, run:"
	@echo "     source integration/scripts/venv.sh"
	@echo ""

CFG ?= small
vendor:
	@bash $(ROOT)/integration/scripts/gen_ibex.sh $(CFG)

clean:
	rm -rf $(ROOT)/build
