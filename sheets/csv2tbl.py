#!v/bin/python3
import csv
import tabulate
import sys

rdr = csv.reader(open(sys.argv[1], newline=''))
headers = next(rdr)
print(tabulate.tabulate(rdr, headers=headers))

