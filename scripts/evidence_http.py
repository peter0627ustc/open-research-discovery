#!/usr/bin/env python3
"""Real HTTP evidence retrieval; never substitutes model text for search hits."""
import argparse
import datetime
import html
import ipaddress
import json
import re
import socket
import subprocess
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


def public_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username:
        raise ValueError('Only public HTTP(S) URLs without credentials are allowed')
    for item in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)):
        if not ipaddress.ip_address(item[4][0]).is_global:
            raise ValueError('Private/local network targets are not evidence sources')


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def request(url):
    public_url(url)
    req = urllib.request.Request(url, headers={'User-Agent': 'OPR-Evidence/1.0 (academic literature audit)'})
    with urllib.request.build_opener(PublicRedirect()).open(req, timeout=45) as response:
        body = response.read(12_000_001)
        if len(body) > 12_000_000:
            raise ValueError('Response exceeds 12 MB; inspect a smaller source')
        return body, response.headers.get('Content-Type', ''), response.url


def search(query, limit=8, route='crossref'):
    if route == 'web':
        url = 'https://www.bing.com/search?' + urllib.parse.urlencode({'format': 'rss', 'q': query})
        body, _, _ = request(url)
        root = ET.fromstring(body)
        items = [{'title': x.findtext('title'), 'url': x.findtext('link'),
                  'snippet': x.findtext('description'), 'content_level': 'search_snippet'}
                 for x in root.findall('./channel/item')][:limit]
    else:
        url = 'https://api.crossref.org/works?' + urllib.parse.urlencode({'query.bibliographic': query, 'rows': limit})
        body, _, _ = request(url)
        data = json.loads(body)
        items = [{'title': x.get('title', []), 'doi': x.get('DOI'), 'url': x.get('URL'),
                  'author': x.get('author', []), 'published': x.get('published'),
                  'abstract': x.get('abstract'), 'link': x.get('link', []),
                  'content_level': 'abstract' if x.get('abstract') else 'metadata'}
                 for x in data['message']['items']]
    if not items:
        return {'status': 'no_results', 'route': route, 'query': query, 'request_url': url, 'items': []}
    if any(not x.get('url') or not x.get('title') for x in items):
        raise ValueError('Search response lacks titles/URLs')
    return {'status': 'ok', 'route': route, 'query': query, 'request_url': url, 'items': items}


def fetch(url):
    body, kind, final_url = request(url)
    if body.startswith(b'%PDF'):
        with tempfile.TemporaryDirectory(prefix='opr-evidence-') as folder:
            from pathlib import Path
            path = Path(folder) / 'source.pdf'
            path.write_bytes(body)
            result = subprocess.run(['pdftotext', '-layout', str(path), '-'], capture_output=True, timeout=45, check=True)
            text = result.stdout.decode('utf-8', errors='replace')
    else:
        text = body.decode('utf-8', errors='replace')
        if 'html' in kind:
            text = re.sub(r'<(script|style)\b[^>]*>.*?</\1>', '', text, flags=re.S | re.I)
            text = html.unescape(re.sub('<[^>]+>', ' ', text))
            text = re.sub(r'[ \t]+', ' ', text)
    if not text.strip():
        raise ValueError('Response contained no readable content')
    return {'status': 'ok', 'url': url, 'final_url': final_url, 'content_type': kind,
            'truncated': len(text) > 100000, 'text': text[:100000],
            'warning': 'HTTP success is not proof of full text: identify paywalls, bot challenges and content level.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    query = sub.add_parser('search')
    query.add_argument('query')
    query.add_argument('--route', choices=['crossref', 'web'], default='crossref')
    query.add_argument('--limit', type=int, choices=range(1, 21), default=8)
    page = sub.add_parser('fetch')
    page.add_argument('url')
    args = parser.parse_args()
    try:
        result = search(args.query, args.limit, args.route) if args.command == 'search' else fetch(args.url)
    except Exception as exc:
        result = {'status': 'retrieval_failed', 'error_type': type(exc).__name__}
    result['retrieved_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result['status'] == 'retrieval_failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
