from datetime import datetime
from fastapi import FastAPI, Response
from fastapi.responses import PlainTextResponse, HTMLResponse
import csv
import io
import json

from googleapiclient.errors import HttpError
from lyrics_utils import do_fetch_setlist, format_setlist, input_form, fetch_override
import set_utils

from sqlmodel import SQLModel, create_engine, Field, Session, select
from typing import Optional
from contextlib import asynccontextmanager
import config

import logging
import sys

logging.basicConfig(
    level=logging.DEBUG,
    stream=sys.stderr,
    format='%(asctime)s %(name)s:%(levelname)s %(message)s'

)
log = logging.getLogger(__name__)

class Lyrics(SQLModel, table=True):
    song: str = Field(primary_key=True)
    artist: str = Field(primary_key=True)
    mtime: Optional[datetime] = Field(default=None)
    lyrics: str

sqlite_url = f"sqlite:///{config.SQLITE_FILE}"

engine = create_engine(sqlite_url)

# arrange for the DB to be created on app startup
@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(engine)
    yield

app = FastAPI(lifespan=lifespan)


def alert_html(msg: str) -> str:
    """A <script>alert(...)</script> snippet; msg may hold quotes, newlines or markup."""
    return '<script>alert(' + json.dumps(msg).replace('</', '<\\/') + ')</script>'


class SetLookupError(Exception):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.message, self.status = message, status


@app.exception_handler(SetLookupError)
async def set_lookup_error(request, exc: SetLookupError):
    # /setlist_full is fetched by other programs, so give them plain text
    if request.url.path == '/setlist_full':
        return PlainTextResponse(exc.message, status_code=exc.status)
    return HTMLResponse(alert_html(exc.message), status_code=exc.status)


def find_set_checked(sheetid, date) -> list[dict]:
    """set_utils.find_set, with bad input and Google API failures turned into SetLookupError."""
    if date:
        try:
            set_utils.date_to_int(date)
        except ValueError:
            raise SetLookupError(f'Bad date: {date}', 400)
    try:
        return set_utils.find_set(sheetid, None, date)
    except HttpError as e:
        log.exception(f'sheet lookup failed {sheetid=} {date=}')
        status = getattr(e.resp, 'status', None)
        if status in (403, 404):
            raise SetLookupError('Could not open that spreadsheet (not found or not shared)', 404)
        raise SetLookupError('Could not read the spreadsheet right now; try again', 502)


@app.get('/lyrics')
async def do_lyrics(
    setlist: str|None = None,
    date: str|None = None,
    html: str|None = None,
    sheetid: str|None = None,
    nocache: str|None = None,
    ) -> Response:

    dohtml:bool = html is not None
    nocache:bool = nocache is not None

    if not (setlist or date or sheetid):
        response = HTMLResponse(input_form('/lyrics', dateonly=False))
        return response

    set_with_lyrics: list[dict] = []
    if date or sheetid:
        rows = find_set_checked(sheetid, date)
        if not rows:
            return HTMLResponse(content=alert_html(f'Oops, no set found for {date or sheetid}'))
        set_with_lyrics = [{'song' :r.get('song'), 'artist': r.get('artist'), 'lyrics':None} for r in rows]
    elif setlist:
        setlist_lines = setlist.split('\n')
        for sl in setlist_lines:
            sl = sl.strip()
            if len(sl) == 0:
                continue
            log.info(f'setlist input: {sl}')
            # "song,artist" per line; csv rules so that "Hello, Goodbye",Beatles works
            try:
                fields = next(csv.reader([sl]))
            except (csv.Error, StopIteration):
                fields = []
            if len(fields) != 2 or not fields[0].strip() or not fields[1].strip():
                raise SetLookupError(f'Expected "song,artist" on each line, got: {sl}', 400)
            set_with_lyrics.append({'song': fields[0].strip(), 'artist': fields[1].strip(), 'lyrics': None})

    # load any cached lyrics
    if not nocache:
        with Session(engine) as session:
            for row in set_with_lyrics:
                song, artist = row.get('song'), row.get('artist')
                if song and artist:
                    results = session.exec(
                        select(Lyrics).where(
                            Lyrics.song == song,
                            Lyrics.artist == artist
                        )
                    )
                    if db_obj := results.one_or_none():
                        log.info(f'found db lyrics for {song}, {artist}')
                        row['lyrics'] = db_obj.lyrics

                    # whether or not we found it, look for overrides
                    mtime, override_lyrics = fetch_override(song, artist)
                    if override_lyrics:
                        db_mtime = getattr(db_obj, 'mtime', None)
                        if (
                           (not db_obj or not db_obj.lyrics) or
                           (not db_mtime or (mtime > db_obj.mtime))
                           ):
                            log.info(f'replacing {song}, {artist} {db_mtime.isoformat() if db_mtime else ''} with override lyrics {mtime.isoformat()}')
                            new_obj = Lyrics(
                                song=song,
                                artist=artist,
                                lyrics=override_lyrics,
                                mtime = mtime)
                            row['lyrics'] = override_lyrics
                            session.merge(new_obj)
                            session.commit()
    else:
        log.info('Nocache set, not using db')

    # fetch any that are not already set
    failures, fetched_set = do_fetch_setlist(set_with_lyrics)

    # save any lyrics we just fetched
    with Session(engine) as session:
        for row, newrow in zip(set_with_lyrics, fetched_set):
            if row.get('lyrics') is None and newrow.get('lyrics') is not None:
                log.info(f'got new lyrics for {newrow["song"]} {newrow["artist"]}')
                obj = Lyrics(
                    song=newrow['song'],
                    artist=newrow['artist'],
                    lyrics=newrow['lyrics'],
                    mtime=datetime.now()
                )
                log.info(f'persisting new lyrics for {newrow["song"]} {newrow["artist"]}')

                merged = session.merge(obj)
                session.add(merged)
        session.commit()

    formatted_lyrics = format_setlist(fetched_set, dohtml)

    if failures:
        dialog = alert_html('\n'.join(['NOT_FOUND:', ''] + failures))
    else:
        dialog = ''
    response = HTMLResponse(content=dialog + formatted_lyrics)
    response.charset = 'utf-8'
    return response


@app.get('/setlist')
async def do_setlist(
    date: str|None = None,
    sheetid: str|None = None,
    ) -> Response:

    if not (date or sheetid):
        return HTMLResponse(input_form('/setlist', dateonly=True))

    rows = find_set_checked(sheetid, date)
    if not rows:
        return HTMLResponse(content=alert_html(f'Oops, no set found for {date or sheetid}'))

    # csv quoting (incl. embedded quotes) so the output can be pasted back into /lyrics
    buf = io.StringIO()
    csv.writer(buf, lineterminator='\n').writerows(
        [(row.get('song') or '', row.get('artist') or '') for row in rows])
    setlist = buf.getvalue().splitlines()
    setlist.append('')
    setlist.extend([f'{row.get("artist")} - {row.get("song")}' for row in rows])
    setlist.append('')

    response = PlainTextResponse(content='\n'.join(setlist))
    response.charset = 'utf-8'
    return response


@app.get('/setlist_full')
async def do_setlist_full(
    date: str|None = None,
    sheetid: str|None = None,
    ) -> Response:

    if not (date or sheetid):
        return HTMLResponse(input_form('/setlist_full', dateonly=True))

    rows = find_set_checked(sheetid, date)
    if not rows:
        return PlainTextResponse(content=f'no set found for {date or sheetid}', status_code=404)

    # fixed header, so every row (and every set) has the same columns
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=set_utils.cleanfields(set_utils.ALLFIELDS), lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)

    # no Content-Disposition: displayed inline, for fetching via XHR
    response = PlainTextResponse(content=buf.getvalue(), media_type='text/csv')
    response.charset = 'utf-8'
    return response
