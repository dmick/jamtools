# for google_utils (will have os.path.expanduser called on it)
DEFAULT_CREDFILE = '~/.config/googleserviceaccount.key'

# Google Sheet id for sheet containing date,sheetid
ALL_SETLISTS_SHEETID = '1hxuvHuYAYcxQlOE4KCaeoiSrgZC95OOk3B2Ciu6LAiM'

# db file path for lyrics cache
SQLITE_FILE = "/home/dmick/src/jamtools/lyrics.db"

# HTML lyrics display: CSS and SCROLL_SCRIPT
CSS = '''
<style>
html {
        scroll-behavior: smooth;
}
p {
        margin: 0px 10px 0px 0px;
        color:white;
        background-color: black;
        font-family: sans-serif;
        font-size: 65px;
        font-weight: 500;
        font-style: sans;
        letter-spacing: 0px;
        text-align: center;
}
</style>'''
SCROLL_SCRIPT = '''
<body onkeydown="scrollfunc(event)">
<script>
function scrollfunc(e) {
        let scrolldist = window.innerHeight / 2;
        if (e.key == "PageDown") {
                window.scrollBy(0, scrolldist);
                e.preventDefault();
        } else if (e.key == "PageUp") {
                window.scrollBy(0, -scrolldist);
                e.preventDefault();
        }
}
</script>'''

HEADER = '<html><body>'
FOOTER = '</body></html>'

INPUT_FORM_STYLE = '''
<style>
  body, form, input, textarea, button {
    font-size: 18px;
  }
  #setlistlabel, #setlist {
    display: block;
  }

</style>'''

LYRICS_FORM = '''
<label id="datelabel" for="date">Date</label>
<input type="date" id="date" name="date">
<button type="button" onclick="document.getElementById('date').value=0">Clear date</button>
<p></p>
<label for="setlist" id="setlistlabel">-- OR -- enter a setlist here (lines of song,artist)<br>Date must be cleared:</label>
<p></p>
<textarea id="setlist" name="setlist" rows="5" cols="40">One,U2\nTwo Hearts Beat As One,U2</textarea>
<p></p>
<label for="sheetid">SheetID</label>
<input type="text" id="sheetid" name="sheetid">
<p></p>
<input type=checkbox id="html" name="html" value="true">
<label for="html" id="htmllabel">Output HTML (run show from browser)</label>
<p></p>
<button type="submit" id="go">Go</button>
'''

DATEONLY_FORM = '''
  <label id="datelabel" for="date">Date</label>
  <input type="date" id="date" name="date">
  <p></p>
  <label for="sheetid">SheetID</label>
  <input type="text" id="sheetid" name="sheetid">
  <p></p>
  <button type="submit" id="go">Go</button>
'''
