from fastapi import FastAPI, Response, Query
from fastapi.responses import PlainTextResponse, HTMLResponse

from lyrics_utils import do_fetch_setlist, do_fetch_seq_setlist, do_fetch_song, HEADER, FOOTER, CSS, SCROLL_SCRIPT, input_form
import google_utils
import set_utils
import csv

import os
from concurrent.futures import ThreadPoolExecutor, Future
from sqlmodel import SQLModel, create_engine, Field, Session

app = FastAPI()

class Lyrics(SQLModel, table=True):
    song: str = Field(primary_key=True)
    artist: str = Field(primary_key=True)
    lyrics: str

sqlite_file_name = "/home/dmick/src/jamtools/lyrics.db"
sqlite_url = f"sqlite:///{sqlite_file_name}"

engine = create_engine(sqlite_url, echo=True)

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

async def lifespan(app: FastAPI):
    create_db_and_tables()


@app.get('/lyrics')
async def do_lyrics(
    setlist: str|None = None,
    date: str|None = None,
    html: str|None = None,
    seq: str|None = None,
    ) -> Response:

    dohtml:bool = html is not None
    doseq:bool = seq is not None

    failures = list()
    if not (setlist or date):
        response = HTMLResponse(input_form('/lyrics', dateonly=False))
        return response

    print(f'{setlist=} {date=} {html=} {seq=}')
    if date:
        print(f'{setlist=} {date=} {html=} {seq=}')
        rows = set_utils.find_set(None, None, date)
        setlist = [(r['song'], r['artist']) for r in rows]
        print(f'date, {setlist=}')

    failures, formatted_lyrics = do_fetch_setlist(setlist, dohtml)
    print(f'{failures=}')
    if dohtml:
        formatted_lyrics = '\n'.join((
            HEADER,
            CSS,
            SCROLL_SCRIPT,
            formatted_lyrics,
            FOOTER,
        ))

    if not dohtml:
        formatted_lyrics = '<pre>\n' + formatted_lyrics + '\n</pre>'

    if failures:
        failures = '\\n'.join((['NOT_FOUND:\\n'] + failures))
        dialog = f'<script>alert("{failures}")</script>'
    else:
        dialog = ''
    response = HTMLResponse(content=dialog + formatted_lyrics)
    response.charset = 'utf-8'
    return response


@app.get('/setlist')
async def do_setlist(
    date: str|None = None,
    ) -> PlainTextResponse:

    if not date:
        return HTMLResponse(input_form('/setlist', dateonly=True))

    rows = set_utils.find_set(None, None, date)
    if not rows:
        return PlainTextResponse(content=f'No set found for {date}')

    setlist = [f'{row["song"]}, {row["artist"]}' for row in rows]
    setlist.append('')
    setlist.extend([f'{row["artist"]} - {row["song"]}' for row in rows])
    setlist.append('')

    response = PlainTextResponse(content='\n'.join(setlist))
    response.charset = 'utf-8'
    return response
