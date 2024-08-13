#!/usr/bin/python3


import argparse
import glob
import os.path
import sys
import datetime
from collections import defaultdict
import gzip
import pprint
import re
import json
import mailbox

def getfield(parts, name, stripchars='<>'):
    try:
        field = [i for i in parts if i.startswith(f'{name}=')][0]
        field = field.split('=')[1]
        field = field.strip(stripchars)
        return field
    except IndexError:
        return None

def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument('-t', '--to', help='To address to find')
    ap.add_argument('-o', '--orig-to', help='orig_to address to find')
    ap.add_argument('-T', '--truncate', action='store_true', help='Truncate "to" list')
    ap.add_argument('-j', '--json', action='store_true', help='json output')
    ap.add_argument('-m', '--msgid', action='store_true', help='parse host Sent box for msgids, show from/to/subject/date')
    ap.add_argument('files', nargs='*')
    return ap.parse_args()

def output(msg, args):

    def dt_to_str(dt):
        if isinstance(dt, datetime.datetime):
            return dt.isoformat()
        return '<type unknown>'

    if args.json:
        json.dump(msg, fp=sys.stdout, indent=4, default=dt_to_str)
    else:
        print(f'{msg["dt"]} {msg["msgid"]} from {msg["fr"]}', end='')
        if msg['orig_to']:
            print(f' orig_to {msg["orig_to"]}', end='')
        if args.truncate:
            if args.to:
                to = args.to
            elif args.orig_to:
                to=args.orig_to
            to = [to]
        else:
            to = msg['to']

        print(f' to {", ".join(to)}', end='')
        print(f' status {msg["status"]}', end='')
        if args.truncate:
            print('...')
        else:
            print()

def search_mbox(path, msgids):
    msgs = list()
    mbox = mailbox.mbox(path, create=False)
    for m in mbox.items():
        if m[1]['message-id'].strip('<>') in msgids:
            msgs.append(m[1])
    return msgs
        

def main():
    # msgs[qid] = {'from': fromstr, 'messageid': msgid, 
    #                 'to': list(tostr1, tostr2, ..)}
    msgs = defaultdict(
        lambda: dict(dt=None, fr=None, msgid=None, to=list(), orig_to='')
    )

    args = parse_args()
    if not args.files:
        files = glob.glob('/var/log/mail.log*')
        files.sort(key=os.path.getmtime)
        args.files = files
    for name in args.files:
        if name.endswith('.gz'):
            f = gzip.open(name, 'rt')
        else:
            f = open(name, 'r')

        mtime = os.fstat(f.fileno()).st_mtime
        dt_mtime = datetime.datetime.fromtimestamp(mtime)
        fileyear = dt_mtime.year
        for line in f:
            parts = line.split()
            parts = [p.strip(' ,') for p in parts]

            dt_parts = parts[0:3]
            parts = parts[3:]
            dtval=datetime.datetime.strptime(
                ' '.join(dt_parts) + ' ' + str(fileyear),'%b %d %H:%M:%S %Y')

            # skip hostname, logger name/pid
            parts = parts[2:]

            to = getfield(parts, 'to')
            messageid = getfield(parts, 'message-id')
            fr = getfield(parts, 'from')
            orig_to = getfield(parts, 'orig_to')
            status = getfield(parts, 'status')

            if to or messageid or fr or orig_to:
                qid = parts.pop(0).rstrip(':')
                md = msgs[qid]
                md['dt'] = dtval
                md['status'] = status

            if to:
                md['to'].append(to)

            if messageid:
                md['msgid'] = messageid

            if fr:
                md['fr'] = fr

            if orig_to:
                md['orig_to'] = orig_to

        f.close()

    truncate = False

    msgids = list()
    for qid, msg in sorted(msgs.items(), key=lambda kv: kv[1]['dt']):
        if args.to:
            if not any([re.search(args.to, to) for to in msg['to']]):
                continue
        if args.orig_to:
            if 'orig_to' not in msg or not re.search(args.orig_to, msg['orig_to']):
                continue
        if args.msgid:
            msgids.append(msg['msgid'])
        else:
            output(msg, args)

    if args.msgid:
        for sentmsg in search_mbox('/home/host/mail/Sent', msgids):
            print(f'''
From: {sentmsg.get('from')}
To: {sentmsg.get('to')}
Cc: {sentmsg.get('cc')}
Bcc: {sentmsg.get('bcc')}
Subject: {sentmsg.get('subject')}
Date: {sentmsg.get('date')}
Message-Id: {sentmsg.get('message-id')}'''
            )


if __name__ == "__main__":
    sys.exit(main())
