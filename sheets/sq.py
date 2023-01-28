#!v/bin/python3

import csv
import sqlite3
import sys

"""
# connect
con = sqlite3.connect(name)

# cursor
cur = con.cursor()

# execute SQL
cur.execute('CREATE TABLE sets(...)')

# insert data
cur.execute("INSERT INTO sets VALUES(v1, v2, v3)")
cur.execute("INSERT INTO sets VALUES(?,?,?)", v1, v2, v3)
cur.executemany("INSERT INTO sets VALUES(?,?,?)",  [(v1, v2, v3), (w1, w2, w3)])

# execute with return values
res = cur.execute("SELECT...")
res.fetchone()
for row in res:
    <operate on row>

# commit transaction started by INSERT
con.commit()

con.close()
"""
sets_definition = [
    'date text',
    'songnum int',
    'title text',
    'artist text',
    'vocal text',
    'guitar1 text',
    'guitar2 text',
    'bass text',
    'drums text',
    'keys1 text',
    'keys2 text',
]

fieldmap = {
    'guitar': 'guitar1',
    'keys': 'keys1',
}

cur = None

def db_cursor(name):
    global cur
    con = sqlite3.connect(name)
    return con.cursor()

def db_create_tbl(table, definition):
    s = f'CREATE TABLE IF NOT EXISTS {table} ({",".join(definition)})'
    cur.execute(s)

def db_insert(table, m):
    """
    count = len(m)
    s = f'INSERT INTO {table} ({",".join(m.keys())}) VALUES('
    vallist = []
    for v in m.values():
        if isinstance(v, str):
            vallist.append("'" + v + "'")
        else:
            vallist.append(str(v))
    s += ','.join(vallist) + ')'
    """
    s = f'INSERT INTO {table} (?) VALUES (?)'
    cur.execute(s, (m.keys(), m.items()))

def db_query(table, query=None):
    res = cur.execute(f'SELECT * from {table} {query}')
    return res

def main():
    global cur
    tbl = sys.argv[1]
    cur = db_cursor(tbl + '.db')
    db_create_tbl('sets', sets_definition)
    db_insert(tbl, {'songnum': 1, 'title': 'Title', 'artist': 'Artist'})
    for row in db_query(tbl, 'where songnum = 1'):
        print(row)

    return 0

if __name__ == '__main__':
    sys.exit(main())
