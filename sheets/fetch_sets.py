#!v/bin/python3

import argparse
import csv

import sys
import google_utils
import set_utils


ALL_SETLISTS_SHEETID = '1hxuvHuYAYcxQlOE4KCaeoiSrgZC95OOk3B2Ciu6LAiM'


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('-i', '--id', help='Google spreadsheet ID to read and csv-ize')
    parser.add_argument('-d', '--date', help='Date corresponding to ID')
    parser.add_argument('-s', '--start', help='Output info from date greater than given date')
    parser.add_argument('-l', '--list', action='store_true', help='output only title/artist')
    return parser.parse_args()

def main():
    """Shows basic usage of the Sheets API.
    Prints values from a sample spreadsheet.
    """
    args = parse_args()

    sheetservice = google_utils.get_sheetservice()
    rows = []

    id_and_date_args = int(args.id is not None) + int(args.date is not None)
    if (id_and_date_args == 1):
        print("Must supply both or neither of --date and --id", file=sys.stderr)
        return 1

    if (id_and_date_args and args.start):
        print("--id/--date and --start are mutually exclusive", file=sys.stderr)
        return 1

    if args.id and args.date:
        rows = (set_utils.get_rows(sheetservice, args.date, args.id))
    else:
        # get the cross-reference of dates/setlist sheets
        date_and_ids = set_utils.get_and_retry_on_rate_limit(sheetservice, ALL_SETLISTS_SHEETID, 'A:B')

        output = args.start is None
        for idrow in date_and_ids:
            if len(idrow) != 2:
                break
            sheetdate, sheetid = idrow[0], idrow[1]
            sheetmonth, sheetday, sheetyear = map(int, sheetdate.split('/'))
            if args.start:
                startmonth, startday, startyear = map(int, args.start.split('/'))
            else:
                startmonth, startday, startyear = 1,1,2000
            sheetdate_int = int(f'{sheetyear}{sheetmonth:02d}{sheetday:02d}')
            startdate_int = int(f'{startyear}{startmonth:02d}{startday:02d}')
            if sheetdate_int > startdate_int:
                output = True
            if output:
                rows += set_utils.get_rows(sheetservice, sheetdate, sheetid)

    if args.list:
        fields = ['song', 'artist']
    else:
        fields = set_utils.cleanfields(set_utils.ALLFIELDS)
    cw = csv.DictWriter(sys.stdout, fields, extrasaction='ignore')
    cw.writeheader()
    cw.writerows(rows)


if __name__ == '__main__':
    main()
