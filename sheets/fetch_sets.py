#!/home/dmick/src/sheets/v/bin/python3

import argparse
import csv

import sys
import google_utils
import set_utils


ALL_SETLISTS_SHEETID = '1hxuvHuYAYcxQlOE4KCaeoiSrgZC95OOk3B2Ciu6LAiM'


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('-i', '--id', help='Google spreadsheet ID to read and csv-ize')
    parser.add_argument('-d', '--date', help='Date of set to fetch (MM/DD/YYYY or YYYY-MM-DD)')
    parser.add_argument('-s', '--start', help='Output info from date greater than given date')
    parser.add_argument('-l', '--list', action='store_true', help='output only title,artist')
    parser.add_argument('-L', '--list2', action='store_true', help='output only artist - title')
    return parser.parse_args()

def date_to_int(d):
    if '-' in d:
        # YYYY-MM-DD, like HTML input type=date
        year, month, day = map(int, d.split('-'))
    else:
        # MM/DD/YYYY
        month, day, year = map(int, d.split('/'))
    date_int = int(f'{year}{month:02d}{day:02d}')
    return date_int

def main():
    args = parse_args()

    sheetservice = google_utils.get_sheetservice()
    rows = []

    if ((args.id or args.date) and args.start):
        print("--id/--date and --start are mutually exclusive", file=sys.stderr)
        return 1

    if args.id and args.date:
        rows = (set_utils.get_rows(sheetservice, args.date, args.id))
    else:
        # get the cross-reference of dates/setlist sheets
        date_and_ids = set_utils.get_and_retry_on_rate_limit(sheetservice, ALL_SETLISTS_SHEETID, 'A:B')

        if args.start:
            startdate_int = date_to_int(args.start)
        if args.date:
            date_int = date_to_int(args.date)

        for idrow in date_and_ids:
            if len(idrow) != 2:
                break
            # if args.start, don't output until date is after args.start
            # if args.date, don't output unless date == args.date
            # if we're here, we didn't have both args.id and args.date

            output = False
            sheetdate, sheetid = idrow[0], idrow[1]
            sheetdate_int = date_to_int(sheetdate)
            if args.start:
                if sheetdate_int > startdate_int:
                    output = True
            elif args.date:
                if sheetdate_int == date_int:
                    output = True
            else:
                output = True

            if output:
                rows += set_utils.get_rows(sheetservice, sheetdate, sheetid)

    if args.date and len(rows) == 0:
        print(f'No set found for {args.date}', file=sys.stderr)
        return 1

    if args.list2:
        for r in rows:
            if 'artist' in r and 'song' in r:
                print(f'{r["artist"]} - {r["song"]}')
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
