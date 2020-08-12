#!/usr/bin/env bash
CMD=$1

SERVICES=( run-handler.service utility-handler.service saver.service mppt_plotter.service iv_plotter.service it_plotter.service vt_plotter.service eqe_plotter.service )

for s in "${SERVICES[@]}"
do
	if test "${CMD}" = "link"
	then
		systemctl --user "${CMD}" "$(readlink -m ${s})"
	else
		systemctl --user "${CMD}" "${s}"
	fi
done
