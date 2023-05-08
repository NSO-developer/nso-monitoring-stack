#!/usr/bin/env python3
# -*- mode: python; python-indent: 4 -*-

import copy
import os
import sys
import pprint as pp

pprint = pp.PrettyPrinter(indent=4).pprint


os.chdir(os.path.dirname(__file__)+'/..')
sys.path.append('.')
print(os.getcwd())

import psutil
from process_exporter.main import \
                        find_java_python_processes,\
                        get_process_metrics,\
                        get_process_info, add_metrics


def find_ncs_process():
    for p in psutil.process_iter(['pid', 'name']):
        if p.name() in ['ncs.smp', 'beam.smp']:
            return p
    raise Exception("ncs.smp not running")


def get_metrics(python):
    print()
    print('='*80)
    print()
    m = {}
    for pn, (p, pnc) in python:
        try:
            pm = get_process_info(p)
            print(p.pid, pn)
            pprint(pm)
            print('-'*80)
            m[pn] = pm
            m_pkg = copy.deepcopy(pm)
            for c_pn, c_p in pnc:
                try:
                    pm = get_process_info(c_p)
                    print(c_p.pid, c_pn)
                    pprint(pm)
                    print('-'*80)
                    print()
                    m[c_pn] = pm
                    add_metrics(m_pkg, pm)
                except psutil.NoSuchProcess as e:
                    update = True
            m[pn].update({ (k[0],f'{k[1]}_pkg'): v for k,v in m_pkg.items() })
        except psutil.NoSuchProcess as e:
            update = True
    return m


def main():
    if 1:
        p_ncs = find_ncs_process()
        pprint(p_ncs)
        p_java, p_python = find_java_python_processes(p_ncs)
        pprint(p_java)
        pprint(p_python)
        pprint(get_process_metrics(p_ncs, p_java, p_python))
        print()
    if 0:
        python = [   (   'python-service',
            (   psutil.Process(pid=4478),
                [   (   'python-service-0',
                        psutil.Process(pid=4528))])),
                  ]
        pprint(get_metrics(python))


if __name__ == '__main__':
    main()
