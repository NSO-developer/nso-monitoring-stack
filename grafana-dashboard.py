#!/usr/bin/env python3
# -*- mode: python; python-indent: 4 -*-

import argparse
import json
import sys
import requests

HEADER = {
    'Accept': 'application/json'
}

def parseArgs(args):
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=str,
                        help='host[:port]',
                        default='localhost:3000')
    parser.add_argument('-f', type=str,
                        help='Dashboard json file')
    parser.add_argument('--clean', action='store_true',
                        help='Clean data to make it generically loadable.')
    parser.add_argument('cmd', choices=['list', 'download', 'load'])
    return parser.parse_args(args)


def grafana_api(host, resource, method='GET', data=None):
    url = f'http://{host}{resource}'
    headers = {
#        "Authorization":"Bearer #####API_KEY#####",
        "Content-Type":"application/json",
        "Accept": "application/json"
    }
    r = requests.request(method, url=url, headers=headers, json=data, verify=False)
    return r.json()

def list_dashboards(args):
    print("Dashboards:")
    dbs = grafana_api(args.host, '/api/search?type=dash-db')
    for db in dbs:
        print('{:-5}  {:10}  {}'.format(db['id'], db['uid'], db['title']))

def download_dashboards(args):
    print("Downloading:")
    dbs = grafana_api(args.host, '/api/search?type=dash-db')
    uids = [(db['uid'], db['title']) for db in dbs]
    for uid,title in uids:
        db_json = grafana_api(args.host, f'/api/dashboards/uid/{uid}')
        fname = f'dashboards_{title.replace(" ", "_")}.json'
        data = json.dumps(db_json, indent=4)
        print(f'Saving {fname}')
        open(fname, 'w').write(data)

def load_dashboard(args):
    db_json = json.load(open(args.f))
    if 'dashboard' not in db_json:
        db_json = {
            'dashboard': db_json
        }
    if args.clean:
        if 'meta' in db_json.keys():
            del db_json['meta']
        db_json['dashboard'].update({
            'id': None,
            'uid': None
        })
        db_json.update({
            'folderId': 0,
            'folderUid': "NSOMonStac",
            'message': "Initial version",
            'overwrite': False
        })
    print("Loading:")
    r = grafana_api(args.host, f'/api/dashboards/db', method='POST',
                               data=db_json)
    print(r)


commands = {
    'list': list_dashboards,
    'download': download_dashboards,
    'load': load_dashboard,
}

def main(args):
    commands[args.cmd](args)

if __name__ == '__main__':
    main(parseArgs(sys.argv[1:]))
