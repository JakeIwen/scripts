"""Parse IPT's optional-end-tag table, rejecting unknown column layouts."""
import re
from html.parser import HTMLParser
from pi.apps.media_organizer.storage import now
from .records import Candidate


class ParseError(ValueError):
    pass


class Fragment(HTMLParser):
    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.text = []
        self.links = []
        self.images = []
        self.freeleech = False
        self.active_link = None
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a':
            self.active_link = [attrs.get('href', ''), '']
            self.links.append(self.active_link)
        if tag in ('img', 'i'):
            self.images.append(attrs.get('alt', ''))
        if 'free' in attrs.get('class', '').split():
            self.freeleech = True

    def handle_endtag(self, tag):
        if tag == 'a':
            self.active_link = None

    def handle_data(self, value):
        self.text.append(value)
        if self.active_link is not None:
            self.active_link[1] += value

    def plain(self):
        return ' '.join(''.join(self.text).split())


def table(markup):
    found = re.search(r'<table\b[^>]*\bid\s*=\s*["\']torrents["\'][^>]*>(.*?)</table>',
                      markup, re.I | re.S)
    if not found:
        raise ParseError('parser could not find the torrent table/columns')
    return found.group(1)


def logged_in(markup):
    return bool(re.search(r'<form\b[^>]*\baction\s*=\s*["\']/lout\.php', markup, re.I))


def cells(markup, tag):
    # IPT omits </td>, </th> and even the header's </tr>. Opening cells delimit them.
    return [Fragment(part) for part in re.split(rf'<{tag}\b[^>]*>', markup, flags=re.I)[1:]]


def columns(header):
    found = {}
    for index, cell in enumerate(cells(header, 'th')):
        label = (' '.join(cell.images) + ' ' + cell.plain()).lower()
        for key in ('size', 'snatches', 'seeders', 'leechers'):
            if key in label:
                found[key] = index
    if len(found) != 4 or len(set(found.values())) != 4:
        raise ParseError('parser could not find the torrent table/columns')
    return found


def size_bytes(value):
    match = re.fullmatch(r'([\d,.]+)\s*(B|KB|MB|GB|TB)', value, re.I)
    if not match:
        raise ParseError('parser could not read torrent size')
    return int(float(match[1].replace(',', '')) * 1024 ** ('B', 'KB', 'MB', 'GB', 'TB').index(match[2].upper()))


def parse_row(markup, indexes, timestamp):
    row = cells(markup, 'td')
    try:
        links = [link for cell in row for link in cell.links]
        title = next(link for link in links if re.fullmatch(r'/t/\d+', link[0]))
        torrent_id = title[0].rsplit('/', 1)[1]
        download = next(link[0] for link in links if link[0].startswith(f'/download.php/{torrent_id}/'))
        category = next(image for image in row[0].images if image)
        subtitle = re.search(r'<div\b[^>]*class=["\']sub["\'][^>]*>(.*?)</div>', markup, re.I | re.S)
        age = Fragment(subtitle[1]).plain().rsplit('|', 1)[-1].strip() if subtitle else ''
        counts = {key: int(row[index].plain().replace(',', ''))
                  for key, index in indexes.items() if key != 'size'}
        return Candidate(torrent_id, title[1].strip(), '', None, None,
                         size_bytes(row[indexes['size']].plain()), **counts,
                         freeleech=any(cell.freeleech for cell in row), age_text=age,
                         category=category, download_path=download, first_seen=timestamp)
    except (StopIteration, IndexError, ValueError):
        raise ParseError('parser could not read torrent table row/columns') from None


def parse(markup):
    content = table(markup)
    # Some pages close thead explicitly; both forms preserve the column contract.
    split = re.split(r'(?:<tbody\b[^>]*>|</thead>)', content, maxsplit=1, flags=re.I)
    if len(split) != 2:
        raise ParseError('parser could not find the torrent table/columns')
    indexes = columns(split[0])
    timestamp = now()
    rows = re.findall(r'<tr\b[^>]*>(.*?)</tr>', split[1], re.I | re.S)
    result = []
    for row in rows:
        if re.search(r'<td\b[^>]*colspan', row, re.I) and re.search(r'no (?:torrents|results)', row, re.I):
            continue
        result.append(parse_row(row, indexes, timestamp))
    remainder = re.sub(r'</?(?:tbody|thead)\b[^>]*>', '', split[1], flags=re.I).strip()
    if (not rows and remainder) or len(re.findall(r'<tr\b', split[1], re.I)) != len(rows):
        raise ParseError('parser could not find the torrent table/columns')
    return result
