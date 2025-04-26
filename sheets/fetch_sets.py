#!/home/dmick/src/jamtools/sheets/v/bin/python3

import argparse
import csv
import datetime

import sys
import google_utils
import set_utils




def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('-i', '--id', help='Google spreadsheet ID to read and csv-ize')
    parser.add_argument('-d', '--date', help='Date of set to fetch (MM/DD/YYYY or YYYY-MM-DD)')
    parser.add_argument('-s', '--start', help='Output info from date greater than given date')
    parser.add_argument('-l', '--list', action='store_true', help='output only title,artist')
    parser.add_argument('-L', '--list2', action='store_true', help='output only artist - title')
    return parser.parse_args()

def main() -> int:
    args = parse_args()

    rows:list[dict[str, str]] = []

    if ((args.id or args.date) and args.start):
        print("--id/--date and --start are mutually exclusive", file=sys.stderr)
        return 1

    rows = set_utils.find_set(args.id, args.start, args.date)

    if args.date and len(rows) == 0:
        print(f'No set found for {args.date}', file=sys.stderr)
        return 1

    if args.list2:
        for row in rows:
            if 'artist' in row and 'song' in row:
                print(f'{row["artist"]} - {row["song"]}')
            else:
                print()
        return 0

    if args.list:
        fields = ['song', 'artist']
    else:
        fields = set_utils.cleanfields(set_utils.ALLFIELDS)
    cw = csv.DictWriter(sys.stdout, fields, extrasaction='ignore')
    cw.writeheader()
    cw.writerows(rows)
    return 0


if __name__ == '__main__':
    sys.exit(main())
