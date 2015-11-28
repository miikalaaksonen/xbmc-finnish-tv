#!/usr/bin/env python2
# -*- coding: utf-8 -*-

"""
yle-dl - download videos from Yle servers

Copyright (C) 2010-2015 Antti Ajanki <antti.ajanki@iki.fi>

This script downloads video and audio streams from Yle Areena
(http://areena.yle.fi) and Elävä Arkisto
(http://yle.fi/aihe/elava-arkisto).
"""

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import sys
import urllib
import urllib2
import re
import subprocess
import os
import os.path
import platform
import signal
import urlparse
import htmlentitydefs
import json
import xml.dom.minidom
import time
import codecs
import base64
import ctypes
import ctypes.util
import itertools
from Crypto.Cipher import AES

version = '2.9.0'

AREENA_NG_SWF = 'http://areena.yle.fi/static/player/1.2.8/flowplayer/flowplayer.commercial-3.2.7-encrypted.swf'
AREENA_NG_HTTP_HEADERS = {'User-Agent': 'yle-dl/' + version.split(' ')[0]}

ARKISTO_SWF = 'http://yle.fi/elavaarkisto/flowplayer/flowplayer.commercial-3.2.7.swf?0.7134730119723827'
RTMPDUMP_OPTIONS_ARKISTO = ['-s', ARKISTO_SWF, '-m', '60']

RTMP_SCHEMES = ['rtmp', 'rtmpe', 'rtmps', 'rtmpt', 'rtmpte', 'rtmpts']

DEFAULT_PROTOCOLS = ['hds', 'hds:youtubedl', 'rtmp']

# list of all options that require an argument
ARGOPTS = ('--rtmp', '-r', '--host', '-n', '--port', '-c', '--socks',
           '-S', '--swfUrl', '-s', '--tcUrl', '-t', '--pageUrl', '-p',
           '--app', '-a', '--swfhash', '-w', '--swfsize', '-x', '--swfVfy',
           '-W', '--swfAge', '-X', '--auth', '-u', '--conn', '-C',
           '--flashVer', '-f', '--subscribe', '-d', '--flv', '-o',
           '--timeout', '-m', '--start', '-A', '--stop', '-B', '--token',
           '-T', '--skip', '-k', '--buffer', '-b')

# rtmpdump exit codes
RD_SUCCESS = 0
RD_FAILED = 1
RD_INCOMPLETE = 2

debug = False
excludechars_linux = '*/|'
excludechars_windows = '\"*/:<>?|'
excludechars = excludechars_linux
rtmpdump_binary = None
hds_binary = ['php', '/usr/local/share/yle-dl/AdobeHDS.php']

libcname = ctypes.util.find_library('c')
libc = libcname and ctypes.CDLL(libcname)

def log(msg):
    enc = sys.stderr.encoding or 'UTF-8'
    sys.stderr.write(msg.encode(enc, 'backslashreplace'))
    sys.stderr.write('\n')
    sys.stderr.flush()

def splashscreen():
    log(u'yle-dl %s: Download media files from Yle Areena and Elävä Arkisto' % version)
    log(u'Copyright (C) 2009-2015 Antti Ajanki <antti.ajanki@iki.fi>, license: GPLv3')

def usage():
    """Print the usage message to stderr"""
    splashscreen()
    log(u'')
    log(u'%s [options] URL' % sys.argv[0])
    log(u'')
    log(u'options:')
    log(u'')
    log(u'-o filename             Save stream to the named file')
    log(u'--latestepisode         Download the latest episode')
    log(u"--showurl               Print URL, don't download")
    log(u"--showtitle             Print stream title, don't download")
    log(u"--showepisodepage       Print web page for each episode")
    log(u'--vfat                  Create Windows-compatible filenames')
    log(u'--sublang lang          Download subtitles, lang = fin, swe, smi, none or all')
    log(u'--hardsubs              Download stream with hard subs if available')
    log(u'--maxbitrate br         Maximum bitrate stream to download, integer in kB/s')
    log(u'                        or "best" or "worst". Not exact on HDS streams.')
    log(u'--rtmpdump path         Set path to rtmpdump binary')
    log(u'--adobehds cmd          Set command for executing AdobeHDS.php script')
    log(u'                        Default: "php /usr/local/share/yle-dl/AdobeHDS.php"')
    log(u'--destdir dir           Save files to dir')
    log(u'--protocol protos       Downloaders that are tried until one of them')
    log(u'                        succeeds (a comma-separated list). Possible values:')
    log(u'                          hds - AdobeHDS.php')
    log(u'                          hds:youtubedl - youtube-dl')
    log(u'                          rtmp - rtmpdump')
    log(u'--pipe                  Dump stream to stdout for piping to media player')
    log(u'                        E.g. "yle-dl --pipe URL | vlc -"')
    log(u'-V, --verbose           Show verbose debug output')

def download_page(url):
    """Returns contents of a HTML page at url."""
    if url.find('://') == -1:
        url = 'http://' + url
    if '#' in url:
        url = url[:url.find('#')]

    request = urllib2.Request(url, headers=AREENA_NG_HTTP_HEADERS)
    try:
        urlreader = urllib2.urlopen(request)
        content = urlreader.read()

        charset = urlreader.info().getparam('charset')
        if not charset:
            metacharset = re.search(r'<meta [^>]*?charset="(.*?)"', content)
            if metacharset:
                charset = metacharset.group(1)
        if not charset:
            charset = 'iso-8859-1'

        return unicode(content, charset, 'replace')
    except urllib2.URLError, exc:
        log(u"Can't read %s: %s" % (url, exc))
        return None
    except ValueError:
        log(u'Invalid URL: ' + url)
        return None

def encode_url_utf8(url):
    """Encode the path component of url to percent-encoded UTF8."""
    (scheme, netloc, path, params, query, fragment) = urlparse.urlparse(url)

    path = path.encode('UTF8')

    # Assume that the path is already encoded if there seems to be
    # percent encoded entities.
    if re.search(r'%[0-9A-Fa-f]{2}', path) is None:
        path = urllib.quote(path, '/+')

    return urlparse.urlunparse((scheme, netloc, path, params, query, fragment))

def decode_html_entity(entity):
    if not entity:
        return u''

    try:
        x = htmlentitydefs.entitydefs[entity]
    except KeyError:
        x = entity

    if x.startswith('&#') and x[-1] == ';':
        x = x[1:-1]

    if x[0] == '#':
        try:
            return unichr(int(x[1:]))
        except (ValueError, OverflowError):
            return u'?'
    else:
        return unicode(x, 'iso-8859-1', 'ignore')

def replace_entitydefs(content):
    return re.sub(r'&(.*?);', lambda m: decode_html_entity(m.group(1)), content)

def int_or_else(x, default):
    try:
        return int(x)
    except ValueError:
        return default

def downloader_factory(url, protocols):
    if url.startswith('http://yle.fi/aihe/') or \
            url.startswith('http://areena.yle.fi/26-') or \
            url.startswith('http://arenan.yle.fi/26-'):
        return RetryingDownloader(ElavaArkistoDownloader, protocols)
    elif url.startswith('http://svenska.yle.fi/artikel/'):
        return RetryingDownloader(ArkivetDownloader, protocols)
    elif url.startswith('http://areena.yle.fi/tv/suora/') or \
            url.startswith('http://arenan.yle.fi/tv/direkt/'):
        return RetryingDownloader(AreenaLiveDownloader, protocols)
    elif re.match(r'^http://(www\.)?yle\.fi/radio/[a-zA-Z0-9]+/suora/?$', url):
        return RetryingDownloader(AreenaLiveRadioDownloader, protocols)
    elif url.startswith('http://areena-v3.yle.fi/') or \
            url.startswith('http://arenan-v3.yle.fi/'):
        return RetryingDownloader(AreenaNGDownloader, protocols)
    elif url.startswith('http://areena.yle.fi/tv/suorat/'):
        return RetryingDownloader(Areena2014LiveTVDownloader, protocols)
    elif url.startswith('http://yle.fi/uutiset/') or \
            url.startswith('http://yle.fi/urheilu/'):
        return RetryingDownloader(YleUutisetDownloader, protocols)
    elif url.startswith('http://areena.yle.fi/') or \
            url.startswith('http://arenan.yle.fi/') or \
            url.startswith('http://yle.fi/'):
        return RetryingDownloader(Areena2014Downloader, protocols)
    else:
        return None

def bitrate_from_arg(arg):
    if arg == 'best':
        return sys.maxint
    elif arg == 'worst':
        return 0
    else:
        try:
            return int(arg)
        except ValueError:
            log(u'Invalid bitrate %s, defaulting to best' % arg)
            arg = sys.maxint

def which(program):
    """Search for program on $PATH and return the full path if found."""
    # Adapted from
    # http://stackoverflow.com/questions/377017/test-if-executable-exists-in-python

    def is_exe(fpath):
        return os.path.isfile(fpath) and os.access(fpath, os.X_OK)

    fpath, fname = os.path.split(program)
    if fpath:
        if is_exe(program):
            return program
    else:
        for path in os.environ["PATH"].split(os.pathsep):
            exe_file = os.path.join(path, program)
            if is_exe(exe_file):
                return exe_file

    return None

def partition(pred, iterable):
    it1, it2 = itertools.tee(iterable)
    return (itertools.ifilter(pred, it1), itertools.ifilterfalse(pred, it2))

def parse_rtmp_single_component_app(rtmpurl):
    """Extract single path-component app and playpath from rtmpurl."""
    # YLE server requires that app is the first path component
    # only. By default librtmp would take the first two
    # components (app/appInstance).
    #
    # This also means that we can't rely on librtmp's playpath
    # parser and have to duplicate the logic here.
    k = 0
    if rtmpurl.find('://') != -1:
        i = -1
        for i, x in enumerate(rtmpurl):
            if x == '/':
                k += 1
                if k == 4:
                    break

        playpath = rtmpurl[(i+1):]
        app_only_rtmpurl = rtmpurl[:i]
    else:
        playpath = rtmpurl
        app_only_rtmpurl = ''

    ext = os.path.splitext(playpath)[1]
    if ext == '.mp4':
        playpath = 'mp4:' + playpath
        ext = '.flv'
    elif ext == '.mp3':
        playpath = 'mp3:' + playpath[:-4]

    return (app_only_rtmpurl, playpath, ext)


class StreamFilters(object):
    """Parameters for deciding which of potentially multiple available stream
    versions to download.
    """
    def __init__(self, latest_only, sublang, hardsubs, maxbitrate):
        self.latest_only = latest_only
        self.sublang = sublang
        self.hardsubs = hardsubs
        self.maxbitrate = maxbitrate

    def keep_lowest_bitrate(self):
        return self.maxbitrate <= 0


# Areena

class AreenaUtils(object):
    def areena_decrypt(self, data, aes_key):
        try:
            bytestring = base64.b64decode(str(data))
        except (UnicodeEncodeError, TypeError):
            return None

        iv = bytestring[:16]
        ciphertext = bytestring[16:]
        padlen = 16 - (len(ciphertext) % 16)
        ciphertext = ciphertext + '\0'*padlen

        decrypter = AES.new(aes_key, AES.MODE_CFB, iv, segment_size=16*8)
        return decrypter.decrypt(ciphertext)[:-padlen]


    def download_subtitles(self, subtitles, filters, videofilename):
        if not filters.hardsubs:
            preferred_lang = filters.sublang
            basename = os.path.splitext(videofilename)[0]
            for sub in subtitles:
                lang = sub.language
                if lang == preferred_lang or preferred_lang == 'all':
                    if sub.url:
                        try:
                            enc = sys.getfilesystemencoding()
                            filename = basename + '.' + lang + '.srt'
                            subtitlefile = filename.encode(enc, 'replace')
                            urllib.urlretrieve(sub.url, subtitlefile)
                            self.add_BOM(subtitlefile)
                            log(u'Subtitles saved to ' + filename)
                            if preferred_lang != 'all':
                                return
                        except IOError, exc:
                            log(u'Failed to download subtitles at %s: %s' % (sub.url, exc))

    def add_BOM(self, filename):
        """Add byte-order mark into a file.

        Assumes (but does not check!) that the file is UTF-8 encoded.
        """
        content = open(filename, 'r').read()
        if content.startswith(codecs.BOM_UTF8):
            return

        f = open(filename, 'w')
        f.write(codecs.BOM_UTF8)
        f.write(content)
        f.close()

    def parse_yle_date(self, yledate):
        """Convert strings like 2012-06-16T18:45:00 into a struct_time.

        Returns None if parsing fails.
        """
        try:
            return time.strptime(yledate, '%Y-%m-%dT%H:%M:%S')
        except (ValueError, TypeError):
            return None


class AreenaNGDownloader(AreenaUtils):
    OP_DOWNLOAD = 1
    OP_PRINT_DOWNLOAD_URL = 2
    OP_PRINT_EPISODE_PAGE_URL = 3
    OP_PIPE = 4

    @staticmethod
    def supported_protocols():
        return ['hds', 'rtmp']

    def __init__(self, streaming_protocol):
        if streaming_protocol.split(':', 1)[0] == 'hds':
            self.stream_class_factory = \
                lambda a, b, c:  AreenaHDSStreamUrl(a, b, c, streaming_protocol)
        else:
            self.stream_class_factory = AreenaStreamUrl

    def download_episodes(self, url, filters, rtmpdumpargs, destdir):
        """Extract all episodes (or just the latest episode if
        latest_only is True) from url."""
        return self.process_episodes(url, filters, rtmpdumpargs, destdir,
                                     self.OP_DOWNLOAD)

    def print_urls(self, url, print_episode_url, filters):
        """Extract episodes from url and print their
        librtmp-compatible URLs on stdout."""
        optype = (self.OP_PRINT_EPISODE_PAGE_URL if (print_episode_url)
            else self.OP_PRINT_DOWNLOAD_URL)
        return self.process_episodes(url, filters, [], None, optype)

    def pipe(self, url, filters):
        return self.process_episodes(url, filters, [], None, self.OP_PIPE)

    def print_titles(self, url, filters):
        playlist = self.get_playlist(url, filters.latest_only)
        if not playlist:
            return RD_FAILED

        enc = sys.getfilesystemencoding()
        for clip in playlist:
            print self.get_clip_title(clip).encode(enc, 'replace')

        return RD_SUCCESS

    def process_episodes(self, url, filters, rtmpdumpargs, destdir, optype):
        playlist = self.get_playlist(url, filters.latest_only)
        if not playlist:
            return RD_FAILED

        overall_status = RD_SUCCESS
        for clip in playlist:

            # Areena's "all episodes" page does not include subtitle information, only
            # the single episode pages do. So read the episode numbers from the "all
            # episodes" page and then download the episodes individually.
            if (clip.has_key('id') and not clip['id'] in url and
                not clip.has_key('subtitles') and optype == self.OP_DOWNLOAD and
                clip['type'] == 'video'):
                url = "http://areena.yle.fi/tv/" + clip['id']
                print url

                res = self.download_episodes(url, filters, rtmpdumpargs, destdir)
            else:
                res = self.process_single_episode(clip, url, filters,
                                                  rtmpdumpargs, destdir, optype)
            if res != RD_SUCCESS:
                overall_status = res

        return overall_status

    def process_single_episode(self, clip, pageurl, filters, rtmpdumpargs,
                               destdir, optype):
        """Construct clip parameters and starts a rtmpdump process."""
        streamurl = self.stream_class_factory(clip, pageurl, filters)
        if not streamurl.is_valid():
            log(u'Unsupported stream at %s: %s' %
                (pageurl, streamurl.get_error_message()))
            return RD_FAILED

        downloader = streamurl.create_downloader(self.get_clip_title(clip),
                                                 destdir, rtmpdumpargs)
        if not downloader:
            log(u'Downloading the stream at %s is not yet supported.' % pageurl)
            log(u'Try --showurl')
            return RD_FAILED

        enc = sys.getfilesystemencoding()
        if optype == self.OP_PRINT_DOWNLOAD_URL:
            print streamurl.to_url().encode(enc, 'replace')
            return RD_SUCCESS
        elif optype == self.OP_PRINT_EPISODE_PAGE_URL:
            print streamurl.to_episode_url().encode(enc, 'replace')
            return RD_SUCCESS
        elif optype == self.OP_PIPE:
            return downloader.pipe()

        outputfile = downloader.output_filename()
        self.download_subtitles(self.clip_subtitles(clip), filters, outputfile)
        return downloader.save_stream()

    def get_clip_title(self, clip):
        if 'channel' in clip:
            # Live radio broadcast
            curtime = time.strftime('-%Y-%m-%d-%H:%M:%S')
            title = clip['channel'].get('name', 'yle-radio') + curtime

        elif 'title' in clip:
            # Video or radio stream
            title = clip['title']
            date = None
            broadcasted = clip.get('broadcasted', None)
            if broadcasted:
                date = broadcasted.get('date', None)
            if not date:
                date = clip.get('published', None)
            if date:
                title += '-' + date.replace('/', '-').replace(' ', '-')

        else:
            title = time.strftime('areena-%Y-%m-%d-%H:%M:%S')

        return title

    def get_playlist(self, url, latest_episode):
        (scheme, netloc, path, params, query, fragment) = urlparse.urlparse(url)
        episodeurl = urlparse.urlunparse((scheme, netloc, path + '.json', params, query, ''))
        fulldata = self.load_metadata(episodeurl)
        if fulldata is None:
            return None

        has_drm = fulldata.get('media', {}).get('protection', 0) >= 3
        if has_drm:
            log(u'This stream is protected with DRM. yle-dl is not able to download this stream.')
            return None

        playlist = []
        if 'contentType' in fulldata or 'channel' in fulldata:
            playlist = [fulldata]
        elif 'search' in fulldata:
            playlist = fulldata['search'].get('results', [])
        elif 'availableEpisodes' in fulldata or \
                'availableClips' in fulldata:
            playlist = self.get_full_series_playlist(url)

        if latest_episode:
            playlist = sorted(playlist, key=self.get_media_time)[-1:]

        return playlist

    def get_full_series_playlist(self, url):
        playlist = []
        (scheme, netloc, path, params, query, fragment) = urlparse.urlparse(url)
        program_types = ['ohjelmat', 'muut']
        for ptype in program_types:
            size_query = 'from=0&to=1000&sisalto=%s' % ptype
            searchurl = urlparse.urlunparse((scheme, netloc, path + '.json',
                                             params, query + size_query, ''))
            fulldata = self.load_metadata(searchurl)
            if fulldata is not None:
                playlist.extend(fulldata.get('search', {}).get('results', []))
        return playlist

    def load_metadata(self, url):
        jsonstr = download_page(url)
        if not jsonstr:
            return None

        if debug:
            log(url)
            log(jsonstr)

        try:
            metadata = json.loads(jsonstr)
        except ValueError:
            log(u'Invalid JSON file at ' +  url)
            return None

        return metadata

    def clip_subtitles(self, clip):
        subdata = clip.get('media', {}).get('subtitles', [])
        return [Subtitle(s.get('url'), s.get('lang', '')) for s in subdata
                if s.get('url')]

    def get_media_time(self, media):
        """Extract date (as struct_time) from media metadata."""
        broadcasted = media.get('broadcasted', {}) or {}
        return self.parse_yle_date(broadcasted.get('date', None)) or \
            self.parse_yle_date(media.get('published', None)) or \
            time.gmtime(0)


### Areena stream URL ###


class AreenaStreamBase(AreenaUtils):
    def __init__(self, clip, pageurl):
        self.error = None
        self.episodeurl = self.create_pageurl(clip) or pageurl

    def is_valid(self):
        return not self.error

    def get_error_message(self):
        if self.is_valid():
            return None
        else:
            return self.error or 'Stream not valid'

    def to_url(self):
        return ''

    def to_episode_url(self):
        return self.episodeurl

    def to_rtmpdump_args(self):
        return None

    def create_pageurl(self, media):
        if not media or 'type' not in media or 'id' not in media:
            return ''

        if media['type'] == 'audio':
            urltype = 'radio'
        else:
            urltype = 'tv'

        return 'http://areena.yle.fi/%s/%s' % (urltype, media['id'])

    def get_metadata(self, clip):
        metadata_page = self.create_pageurl(clip)
        if not metadata_page:
            return None

        jsonurl = metadata_page + '.json'
        jsonstr = download_page(jsonurl)
        if not jsonstr:
            return None

        try:
            clipjson = json.loads(jsonstr)
        except ValueError:
            log(u'Invalid JSON file at ' +  jsonurl)
            return None

        return clipjson

    def full_metadata_if_search_result(self, clip):
        # Search results don't have the media item so we have to
        # download clip metadata from the source.
        if not clip:
            return {}
        elif not clip.has_key('media'):
            clip = self.get_metadata(clip)
        return clip

    def stream_from_papi(self, papiurl, aes_key, filters):
        papi = download_page(papiurl)
        if not papi:
            log('Failed to download papi')
            return None

        if papi.startswith('<media>'):
            papi_decoded = papi
        else:
            papi_decoded = self.areena_decrypt(papi, aes_key)

        if debug:
            log(papi_decoded)

        try:
            papi_xml = xml.dom.minidom.parseString(papi_decoded)
        except Exception as exc:
            log(unicode(exc.message, 'utf-8', 'ignore'))
            return None

        streams = self.all_streams_from_papi(papi_xml)
        if not streams:
            log('No streams found in papi')
            return None

        withsubs = self.filter_by_hard_subtitles(streams, filters)
        best = self.select_quality(withsubs, filters)
        if not best:
            log('No streams matching the bitrate limit')
            return None

        if debug:
            log('Selected stream')
            log(str(best))

        return best

    def all_streams_from_papi(self, papi):
        streams = []
        assets = papi.getElementsByTagName('onlineAsset')
        for asset in assets:
            urls = asset.getElementsByTagName('url')
            if urls and len(urls) >= 1:
                connect = self.extract_child_content(urls[0], 'connect')
                stream = self.extract_child_content(urls[0], 'stream')
                if connect:
                    videoBR = self.extract_child_content(asset, 'videoBitrate') or ''
                    audioBR = self.extract_child_content(asset, 'audioBitrate') or ''
                    subtitles = self.extract_child_content(asset, 'hardSubtitles') or ''
                    streams.append(PAPIStream(connect, stream, videoBR,
                                              audioBR, subtitles))
        return streams

    def extract_child_content(self, node, childName):
        child = node.getElementsByTagName(childName)
        if child.length > 0 and child[0].firstChild:
            return child[0].firstChild.nodeValue
        else:
            return None

    def filter_by_hard_subtitles(self, streams, filters):
        if filters.hardsubs and filters.sublang == 'all':
            filtered = streams
        elif filters.hardsubs and filters.sublang != 'none':
            filtered = [s for s in streams if s.hardSubtitles == filters.sublang]
        else:
            filtered = [s for s in streams if not s.hardSubtitles]
        return filtered or streams

    def select_quality(self, streams, filters):
        if filters.keep_lowest_bitrate():
            # lowest quality stream
            return sorted(streams, key=lambda x: x.bitrate())[0]
        else:
            # highest quality stream below maxbitrate
            below_limit = [x for x in streams if x.bitrate() < filters.maxbitrate]
            if below_limit:
                return sorted(below_limit, key=lambda x: x.bitrate())[-1]
            else:
                return None


class AreenaRTMPStreamUrl(AreenaStreamBase):
    # Extracted from
    # http://areena.yle.fi/static/player/1.2.8/flowplayer/flowplayer.commercial-3.2.7-encrypted.swf
    AES_KEY = 'hjsadf89hk123ghk'

    def __init__(self, clip, pageurl):
        AreenaStreamBase.__init__(self, clip, pageurl)
        self.rtmp_params = None

    def is_valid(self):
        return bool(self.rtmp_params)

    def to_url(self):
        return self.rtmp_parameters_to_url(self.rtmp_params)

    def to_rtmpdump_args(self):
        if self.rtmp_params:
            return self.rtmp_parameters_to_rtmpdump_args(self.rtmp_params)
        else:
            return []

    def create_downloader(self, clip_title, destdir, extra_argv):
        if not self.to_rtmpdump_args():
            return None
        else:
            return RTMPDump(self, clip_title, destdir, extra_argv)

    def rtmp_parameters_from_papi(self, papiurl, pageurl, islive, filters):
        stream = self.stream_from_papi(papiurl, self.AES_KEY, filters)
        return self.stream_to_rtmp_parameters(stream, pageurl, islive)

    def stream_to_rtmp_parameters(self, stream, pageurl, islive):
        if not stream:
            return None

        rtmp_connect = stream.connect
        rtmp_stream = stream.stream
        if not rtmp_stream:
            log('No rtmp stream')
            return None

        try:
            scheme, edgefcs, rtmppath = self.rtmpurlparse(rtmp_connect)
        except ValueError as exc:
            log(unicode(exc.message, 'utf-8', 'ignore'))
            return None

        ident = download_page('http://%s/fcs/ident' % edgefcs)
        if ident is None:
            log('Failed to read ident')
            return None

        if debug:
            log(ident)

        try:
            identxml = xml.dom.minidom.parseString(ident)
        except Exception as exc:
            log(unicode(exc.message, 'utf-8', 'ignore'))
            return None

        nodelist = identxml.getElementsByTagName('ip')
        if len(nodelist) < 1 or len(nodelist[0].childNodes) < 1:
            log('No <ip> node!')
            return None
        rtmp_ip = nodelist[0].firstChild.nodeValue

        app_without_fcsvhost = rtmppath.lstrip('/')
        app_fields = app_without_fcsvhost.split('?', 1)
        baseapp = app_fields[0]
        if len(app_fields) > 1:
            auth = app_fields[1]
        else:
            auth = ''
        app = '%s?_fcs_vhost=%s&%s' % (baseapp, edgefcs, auth)
        rtmpbase = '%s://%s/%s' % (scheme, edgefcs, baseapp)
        tcurl = '%s://%s/%s' % (scheme, rtmp_ip, app)

        rtmpparams = {'rtmp': rtmpbase,
                      'app': app,
                      'playpath': rtmp_stream,
                      'tcUrl': tcurl,
                      'pageUrl': pageurl,
                      'swfUrl': AREENA_NG_SWF}
        if islive:
            rtmpparams['live'] = '1'

        return rtmpparams

    def rtmpurlparse(self, url):
        if '://' not in url:
            raise ValueError("Invalid RTMP URL")

        scheme, rest = url.split('://', 1)
        if scheme not in RTMP_SCHEMES:
            raise ValueError("Invalid RTMP URL")

        if '/' not in rest:
            raise ValueError("Invalid RTMP URL")

        server, app_and_playpath = rest.split('/', 1)
        return (scheme, server, app_and_playpath)

    def rtmp_parameters_to_url(self, params):
        components = [params['rtmp']]
        for key, value in params.iteritems():
            if key != 'rtmp':
                components.append('%s=%s' % (key, value))
        return ' '.join(components)

    def rtmp_parameters_to_rtmpdump_args(self, params):
        args = []
        for key, value in params.iteritems():
            if key == 'live':
                args.append('--live')
            else:
                args.append('--%s=%s' % (key, value))
        return args


class AreenaStreamUrl(AreenaRTMPStreamUrl):
    def __init__(self, clip, pageurl, filters):
        AreenaRTMPStreamUrl.__init__(self, clip, pageurl)
        self.direct_url = None
        if clip:
            if 'channel' in clip:
                self._initialize_liveradio_parameters(clip, pageurl, filters)
            else:
                self._initialize_tv_stream(clip, pageurl, filters)

    def is_valid(self):
        return bool(self.rtmp_params) or bool(self.direct_url)

    def to_url(self):
        if self.rtmp_params:
            return self.rtmp_parameters_to_url(self.rtmp_params)
        elif self.direct_url:
            return self.direct_url
        else:
            return ''

    def _initialize_liveradio_parameters(self, clip, pageurl, filters):
        channel = clip.get('channel', {})
        lang = channel.get('lang', 'fi')
        radioid = channel.get('id', None)
        if not radioid:
            self.error = 'id missing'
            return

        papiurl = 'http://papi.yle.fi/ng/radio/rtmp/%s/%s' % (radioid, lang)
        self.rtmp_params = self.rtmp_parameters_from_papi(papiurl, pageurl, True, filters)

    def _initialize_tv_stream(self, clip, pageurl, filters):
        clip = self.full_metadata_if_search_result(clip) or {}
        media = clip.get('media', {})
        if media.get('id'):
            self._parse_rtmp_url(media, pageurl, filters)
        elif media.get('mediaUrl'):
            self._parse_direct_media_url(media)
        elif media.get('downloadUrl'):
            self._parse_direct_download_url(media)
        else:
            self.error = 'No id, mediaUrl or downloadUrl'

    def _parse_rtmp_url(self, media, pageurl, filters):
        if media.get('live', False):
            islive = True
            papiurl = 'http://papi.yle.fi/ng/live/rtmp/' + media['id'] + '/fin'
        else:
            islive = False
            papiurl = 'http://papi.yle.fi/ng/mod/rtmp/' + media['id']

        self.rtmp_params = \
          self.rtmp_parameters_from_papi(papiurl, pageurl, islive, filters)

    def _parse_direct_media_url(self, media):
        self.direct_url = media.get('mediaUrl')

    def _parse_direct_download_url(self, media):
        self.direct_url = media.get('downloadUrl')


class AreenaHDSStreamUrl(AreenaStreamBase):
    # Extracted from
    # http://areena.yle.fi/static/player/1.3.12/flowplayer/flowplayer.commercial-3.2.16-encrypted.swf
    HDS_AES_KEY = 'C6F258503B21E30A'

    def __init__(self, clip, pageurl, filters, backend):
        AreenaStreamBase.__init__(self, clip, pageurl)
        self.hds_url = self._initialize_hds_stream(clip, pageurl, filters)
        self.maxbitrate = filters.maxbitrate
        if backend == 'hds:youtubedl':
            self.downloader_class = YoutubeDLHDSDump
        else:
            self.downloader_class = HDSDump

    def to_url(self):
        return self.hds_url or ''

    def create_downloader(self, clip_title, destdir, extra_argv):
        return self.downloader_class(self, clip_title, destdir, extra_argv, self.maxbitrate)

    def _initialize_hds_stream(self, clip, pageurl, filters):
        clip = self.full_metadata_if_search_result(clip) or {}
        media = clip.get('media', {})
        if media.get('id'):
            return self._parse_hds_url(media, pageurl, filters)
        else:
            self.error = 'Media ID missing'
            return None

    def _parse_hds_url(self, media, pageurl, filters):
        if media.get('live', False):
            papiurl = 'http://papi.yle.fi/ng/live/hds/' + media['id'] + '/fin'
        else:
            papiurl = 'http://papi.yle.fi/ng/mod/hds/' + media['id']

        stream = self.stream_from_papi(papiurl, self.HDS_AES_KEY, filters)
        if not stream or not stream.connect:
            self.error = 'HDS stream not found'
            return None
        else:
            return stream.connect + '&g=ABCDEFGHIJKL&hdcore=3.3.0&plugin=flowplayer-3.3.0.0'


class Areena2014HDSStreamUrl(AreenaHDSStreamUrl):
    def __init__(self, pageurl, hdsurl, filters, backend):
        AreenaHDSStreamUrl.__init__(self, {}, pageurl, filters, backend)
        self.episodeurl = pageurl
        if hdsurl:
            sep = '&' if '?' in hdsurl else '?'
            self.hds_url = hdsurl + sep + \
                'g=ABCDEFGHIJKL&hdcore=3.3.0&plugin=flowplayer-3.3.0.0'
        else:
            self.hds_url = None
        self.error = None

    def is_valid(self):
        return not self.error

    def get_error_message(self):
        if self.is_valid():
            return None
        else:
            return self.error or 'Stream not valid'

    def to_url(self):
        return self.hds_url

    def to_episode_url(self):
        return self.episodeurl


class Areena2014RTMPStreamUrl(AreenaRTMPStreamUrl):
    def __init__(self, pageurl, streamurl, filters):
        AreenaRTMPStreamUrl.__init__(self, None, pageurl)
        rtmpstream = self.create_rtmpstream(streamurl)
        self.rtmp_params = self.stream_to_rtmp_parameters(rtmpstream, pageurl, False)
        self.rtmp_params['app'] = self.rtmp_params['app'].split('/', 1)[0]

    def create_rtmpstream(self, streamurl):
        (rtmpurl, playpath, ext) = parse_rtmp_single_component_app(streamurl)
        playpath = playpath.split('?', 1)[0]
        return PAPIStream(streamurl, playpath, 0, 0, False)


class HTTPStreamUrl(object):
    def __init__(self, url):
        self.url = url
        path = urlparse.urlparse(url)[2]
        self.ext = os.path.splitext(path)[1] or None

    def is_valid(self):
        return True

    def get_error_message(self):
        return None

    def to_url(self):
        return self.url

    def create_downloader(self, clip_title, destdir, extra_argv):
        return HTTPDump(self, clip_title, destdir, extra_argv)


class InvalidStreamUrl(object):
    def __init__(self, error_message):
        self.error = error_message

    def is_valid(self):
        return False

    def get_error_message(self):
        return self.error

    def to_url(self):
        return ''


class PAPIStream(object):
    def __init__(self, connect, stream, videoBitrate, audioBitrate, hardSubtitles):
        self.connect = connect
        self.stream = stream
        self.videoBitrate = int_or_else(videoBitrate, 0)
        self.audioBitrate = int_or_else(audioBitrate, 0)
        self.hardSubtitles = hardSubtitles

    def __str__(self):
        return json.dumps({
            'connect': self.connect,
            'stream': self.stream,
            'videoBitrate': self.videoBitrate,
            'audioBitrate': self.audioBitrate,
            'hardSubtitles': self.hardSubtitles})

    def bitrate(self):
        return self.videoBitrate + self.audioBitrate


### Areena (the new version with beta introduced in 2014) ###

class Areena2014Downloader(AreenaUtils):
    # Extracted from
    # http://player.yle.fi/assets/flowplayer-1.4.0.3/flowplayer/flowplayer.commercial-3.2.16-encrypted.swf
    AES_KEY = 'yjuap4n5ok9wzg43'

    @staticmethod
    def supported_protocols():
        return ['hds']

    def __init__(self, streaming_protocol):
        self.backend = streaming_protocol

    def download_episodes(self, url, filters, extra_argv, destdir):
        def download_clip(clip):
            downloader = clip.streamurl.create_downloader(clip.title, destdir,
                                                          extra_argv)
            if not downloader:
                log(u'Downloading the stream at %s is not yet supported.' % url)
                log(u'Try --showurl')
                return RD_FAILED

            outputfile = downloader.output_filename()
            self.download_subtitles(clip.subtitles, filters, outputfile)
            return downloader.save_stream()

        return self.process(download_clip, url, filters)

    def print_urls(self, url, print_episode_url, filters):
        def print_clip_url(clip):
            enc = sys.getfilesystemencoding()
            if print_episode_url:
                print_url = clip.streamurl.to_episode_url()
            else:
                print_url = clip.streamurl.to_url()
            print print_url.encode(enc, 'replace')
            return RD_SUCCESS

        return self.process(print_clip_url, url, filters)

    def pipe(self, url, filters):
        def pipe_clip(clip):
            dl = clip.streamurl.create_downloader(clip.title, '', [])
            return dl.pipe()

        return self.process(pipe_clip, url, filters)

    def print_titles(self, url, filters):
        def print_clip_title(clip):
            enc = sys.getfilesystemencoding()
            print clip.title.encode(enc, 'replace')
            return RD_SUCCESS

        return self.process(print_clip_title, url, filters)

    def process(self, clipfunc, url, filters):
        overall_status = RD_SUCCESS
        playlist = self.get_playlist(url, filters)
        for clipurl in playlist:
            res = self.process_single_episode(clipfunc, clipurl, filters)
            if res != RD_SUCCESS:
                overall_status = res
        return overall_status

    def get_playlist(self, url, filters):
        """If url is a series page, return a list of included episode pages."""
        program_list_re = '<ul class="program-list".*?>(.*?)</ul>'
        episode_re = r'<a itemprop="url" href="([^">]+)"'

        playlist = None
        html = download_page(url)
        if html and self.is_playlist_page(html):
            listmatch = re.search(program_list_re, html, re.DOTALL)
            if listmatch:
                programlist = listmatch.group(1)
                hrefs = (m.group(1) for m in
                         re.finditer(episode_re, programlist))
                playlist = [urlparse.urljoin(url, href) for href in hrefs]

        if debug:
            if playlist:
                log('playlist page with %d clips' % len(playlist))
            else:
                log('not a playlist')

        if not playlist:
            playlist = [url]

        if filters.latest_only:
            playlist = playlist[:1]

        return playlist

    def is_playlist_page(self, html):
        playlist_meta = '<meta property="og:type" content="video.tv_show">'
        player_class = 'class="yle_areena_player"'
        return playlist_meta in html or not player_class in html

    def process_single_episode(self, clipfunc, url, filters):
        clip = self.clip_for_url(url, filters)
        if clip.streamurl.is_valid():
            return clipfunc(clip)
        else:
            log(u'Unsupported stream: %s' %
                clip.streamurl.get_error_message())
            return RD_FAILED

    def clip_for_url(self, pageurl, filters):
        pid = self.program_id_from_url(pageurl)
        if not pid:
            return FailedClip(pageurl, 'Failed to parse a program ID')

        program_info = self.load_jsonp(self.program_info_url(pid))
        if not program_info:
            return FailedClip(pageurl, 'Failed to download program data')

        if debug:
            log('program data:')
            log(json.dumps(program_info))

        unavailable = self.unavailable_clip(program_info, pageurl)
        return unavailable or \
          self.create_clip(program_info, pid, pageurl, filters)

    def unavailable_clip(self, program_info, pageurl):
        event = self.publish_event(program_info)
        expired_timestamp = self.event_expired_timestamp(event)
        if expired_timestamp:
            return FailedClip(pageurl, 'The clip has expired on %s' %
                              expired_timestamp)

        future_timestamp = self.event_in_future_timestamp(event)
        if future_timestamp:
            return FailedClip(pageurl, 'The clip will be published at %s' %
                              future_timestamp)

        return None

    def program_info_url(self, program_id):
        return 'http://player.yle.fi/api/v1/programs.jsonp?' \
            'id=%s&callback=yleEmbed.programJsonpCallback' % \
            (urllib.quote_plus(program_id))

    def create_clip(self, program_info, program_id, pageurl, filters):
        media_id = self.program_media_id(program_info)
        if not media_id:
            return FailedClip(pageurl, 'Failed to parse media ID')

        proto = self.program_protocol(program_info)
        medias = self.yle_media_descriptor(media_id, program_id, proto)
        if not medias:
            return FailedClip(pageurl, 'Failed to parse media object')

        selected_media = self.select_media(medias, filters)

        return Clip(pageurl,
                    self.program_title(program_info),
                    self.media_streamurl(selected_media, pageurl, filters),
                    self.media_subtitles(selected_media))

    def yle_media_descriptor(self, media_id, program_id, protocol):
        media_jsonp_url = 'http://player.yle.fi/api/v1/media.jsonp?' \
                          'id=%s&callback=yleEmbed.startPlayerCallback&' \
                          'mediaId=%s&protocol=%s&client=areena-flash-player&instance=1' % \
            (urllib.quote_plus(media_id), urllib.quote_plus(program_id), \
             urllib.quote_plus(protocol))
        media = self.load_jsonp(media_jsonp_url)

        if debug and media:
            log('media:')
            log(json.dumps(media))

        return media

    def program_id_from_url(self, url):
        parsed = urlparse.urlparse(url)
        return parsed.path.split('/')[-1]

    def program_media_id(self, program_info):
        event = self.publish_event(program_info)
        return event.get('media', {}).get('id')

    def event_expired_timestamp(self, event):
        if event.get('temporalStatus') == 'in-past':
            return event.get('endTime')
        else:
            return None

    def event_in_future_timestamp(self, event):
        if event.get('temporalStatus') == 'in-future':
            return event.get('startTime')
        else:
            return None

    def program_title(self, program_info):
        program = program_info.get('data', {}).get('program', {})
        titleObject = program.get('title')
        itemTitleObject = program.get('itemTitle')
        title = self.localized_text(titleObject) or \
                self.localized_text(titleObject, 'sv') or \
                self.localized_text(itemTitleObject) or \
                self.localized_text(itemTitleObject, 'sv') or \
                'areena'

        promoTitleObject = program.get('promotionTitle')
        promotionTitle = self.localized_text(promoTitleObject) or \
          self.localized_text(promoTitleObject, 'sv')
        if promotionTitle and not promotionTitle.startswith(title):
            title += ": " + promotionTitle

        date = self.publish_date(program_info)
        if date:
            title += '-' + date.replace('/', '-').replace(' ', '-')

        return title

    def program_protocol(self, program_info):
        event = self.publish_event(program_info)
        if event.get('media', {}).get('type') == 'AudioObject':
            return 'RTMPE'
        else:
            return 'HDS'

    def publish_date(self, program_info):
        event = self.publish_event(program_info)
        return event.get('startTime')

    def publish_event(self, program_info):
        events = program_info.get('data', {}) \
                             .get('program', {}) \
                             .get('publicationEvent', [])

        has_current = any(self.publish_event_is_current(e) for e in events)
        if has_current:
            events = [e for e in events if self.publish_event_is_current(e)]

        with_media = [e for e in events if e.get('media')]
        if with_media:
            return with_media[0]
        else:
            return {}

    def publish_event_is_current(self, event):
        return event.get('temporalStatus') == 'currently'

    def localized_text(self, alternatives, language='fi'):
        if alternatives:
            return alternatives.get(language) or alternatives.get('fi')
        else:
            return None

    def filter_by_subtitles(self, streams, filters):
        if filters.hardsubs:
            substreams = [s for s in streams if s.has_key('hardsubtitle')]
        else:
            substreams = [s for s in streams if not s.has_key('hardsubtitle')]

        if filters.sublang == 'all':
            filtered = substreams
        else:
            filtered = [s for s in substreams if s.get('lang') == filters.sublang]

        return filtered or streams

    def select_media(self, media,  filters):
        protocol = media.get('meta', {}).get('protocol') or 'HDS'
        mediaobj = media.get('data', {}).get('media', {}).get(protocol, [])
        medias = self.filter_by_subtitles(mediaobj, filters)

        if medias:
            return medias[0]
        else:
            return {}

    def media_streamurl(self, media, pageurl, filters):
        url = media.get('url')
        if not url:
            return InvalidStreamUrl('No media URL')

        decodedurl = self.areena_decrypt(url, self.AES_KEY)
        if not decodedurl:
            return InvalidStreamUrl('Decrypting media URL failed')

        if media.get('protocol') == 'HDS':
            return Areena2014HDSStreamUrl(pageurl, decodedurl, filters, self.backend)
        else:
            return Areena2014RTMPStreamUrl(pageurl, decodedurl, filters)

    def media_subtitles(self, media):
        subtitles = []
        for s in media.get('subtitles', []):
            uri = s.get('uri')
            lang = self.language_code_from_subtitle_uri(uri) or \
              self.three_letter_language_code(s.get('lang'), s.get('type'))
            if uri:
                subtitles.append(Subtitle(uri, lang))
        return subtitles

    def language_code_from_subtitle_uri(self, uri):
        if uri.endswith('.srt'):
            ext = uri[:-4].rsplit('.', 1)[-1]
            if len(ext) <= 3:
                return ext
            else:
                return None
        else:
            return None

    def three_letter_language_code(self, lang, subtype):
        if subtype == 'hearingimpaired':
            return lang + 'h'
        else:
            language_map = {'fi': 'fin', 'sv': 'swe'}
            return language_map.get(lang, lang)

    def load_jsonp(self, url):
        json_string = self.remove_jsonp_padding(download_page(url))
        if not json_string:
            return None

        try:
            json_parsed = json.loads(json_string)
        except ValueError:
            return None

        return json_parsed

    def remove_jsonp_padding(self, jsonp):
        if not jsonp:
            return None

        without_padding = re.sub(r'^[\w.]+\(|\);$','', jsonp)
        if without_padding[:1] != '{' or without_padding[-1:] != '}':
            return None

        return without_padding


class Areena2014LiveTVDownloader(Areena2014Downloader):
    def program_info_url(self, program_id):
        quoted_pid = urllib.quote_plus(program_id)
        return 'http://player.yle.fi/api/v1/services.jsonp?' \
            'id=%s&callback=yleEmbed.simulcastJsonpCallback&' \
            'region=fi&instance=1&dataId=%s' % \
            (quoted_pid, quoted_pid)

    def program_media_id(self, program_info):
        outlets = program_info.get('data', {}).get('outlets') or [{}]
        return outlets[0].get('outlet', {}).get('media', {}).get('id')

    def program_title(self, program_info):
        service = program_info.get('data', {}).get('service', {})
        title = self.localized_text(service.get('title')) or 'areena'
        title += time.strftime('-%Y-%m-%d-%H:%M:%S')
        return title


class YleUutisetDownloader(Areena2014Downloader):
    @staticmethod
    def supported_protocols():
        return Areena2014Downloader.supported_protocols()

    def download_episodes(self, url, filters, extra_argv, destdir):
        return self.delegate_to_areena_downloader(
            'download_episodes', url, filters=filters, extra_argv=extra_argv,
            destdir=destdir)

    def print_urls(self, url, print_episode_url, filters):
        return self.delegate_to_areena_downloader(
            'print_urls', url, print_episode_url=print_episode_url,
             filters=filters)

    def pipe(self, url, filters):
        return self.delegate_to_areena_downloader(
            'pipe', url, filters=filters)

    def print_titles(self, url, filters):
        return self.delegate_to_areena_downloader(
            'print_titles', url, filters=filters)

    def delegate_to_areena_downloader(self, method_name, url, *args, **kwargs):
        areena_urls = self.build_areena_urls(url)
        if areena_urls:
            log(u'Found areena URLs: ' + ', '.join(areena_urls))

            overall_status = RD_SUCCESS
            for url in areena_urls:
                kwcopy = dict(kwargs)
                kwcopy['url'] = url
                method = getattr(super(YleUutisetDownloader, self), method_name)
                res = method(*args, **kwcopy)
                if res != RD_SUCCESS:
                    overall_status = res

            return overall_status
        else:
            log(u'No video stream found at ' + url)
            return RD_FAILED

    def build_areena_urls(self, url):
        html = download_page(url)
        if not html:
            return None

        player_re = r'<div class="media yle_areena_player[^>]*data-id="([0-9-]+)"[^>]*>'
        dataids = re.findall(player_re, html)
        return [self.id_to_areena_url(id) for id in dataids]

    def id_to_areena_url(self, data_id):
        if '-' in data_id:
            areena_id = data_id
        else:
            areena_id = '1-' + data_id
        return 'http://areena.yle.fi/' + areena_id


class Clip(object):
    def __init__(self, pageurl, title, streamurl, subtitles):
        self.pageurl = pageurl
        self.title = title
        self.streamurl = streamurl
        self.subtitles = subtitles


class FailedClip(Clip):
    def __init__(self, pageurl, errormessage):
        Clip.__init__(self, pageurl, None, InvalidStreamUrl(errormessage), None)


class Subtitle(object):
    def __init__(self, url, language):
        self.url = url
        self.language = language


### Areena live TV ###
#
# This is for the real live streams
# (http://areena.yle.fi/tv/suora/...). The old-style discrete live
# broadcasts (http://areena.yle.fi/tv/...) are still handled by
# AreenaNGDownloader.


class AreenaLiveDownloader(object):
    @staticmethod
    def supported_protocols():
        return ['rtmp']

    def __init__(self, streaming_protocols):
        pass

    def download_episodes(self, url, filters, rtmpdumpargs, destdir):
        dl = self._downloader(url, filters, rtmpdumpargs, destdir)
        if not dl:
            return RD_FAILED
        return dl.save_stream()

    def _downloader(self, url, filters, rtmpdumpargs, destdir):
        streamurl = AreenaLiveStreamUrl(url, filters)
        if not streamurl.is_valid():
            return None

        clip_title = self.get_live_stream_title(url)
        return streamurl.create_downloader(clip_title, destdir, rtmpdumpargs)

    def print_urls(self, url, print_episode_url, filters):
        """Extract episodes from url and print their
        librtmp-compatible URLs on stdout."""
        printableurl = (url if print_episode_url
                        else AreenaLiveStreamUrl(url, filters).to_url())
        enc = sys.getfilesystemencoding()
        print printableurl.encode(enc, 'replace')
        return RD_SUCCESS

    def pipe(self, url, filters):
        dl = self._downloader(url, filters, [], '')
        return dl.pipe()

    def print_titles(self, url, filters):
        enc = sys.getfilesystemencoding()
        print self.get_live_stream_title(url).encode(enc, 'replace')
        return RD_SUCCESS

    def get_live_stream_title(self, url):
        title = AreenaLiveStreamUrl.extract_live_channel_from_url(url) or 'yleTV'
        title += time.strftime('-%Y-%m-%d-%H:%M:%S')
        return title


### Areena live stream URL ###


class AreenaLiveStreamUrl(AreenaRTMPStreamUrl):
    def __init__(self, pageurl, filters):
        AreenaRTMPStreamUrl.__init__(self, None, pageurl)
        self.rtmp_params = self._get_live_rtmp_parameters(pageurl, filters)

    @staticmethod
    def extract_live_channel_from_url(url):
        m = re.search(r'http://(?:areena.yle.fi/tv/suora|arenan.yle.fi/tv/direkt)/(.+)', url)
        return m and m.group(1)

    def _get_live_rtmp_parameters(self, url, filters):
        channel = AreenaLiveStreamUrl.extract_live_channel_from_url(url)
        if channel is None:
            return None

        default_media_id = 'yle-' + channel
        fem_mapping = {'fem': 'yle-fem-fi',
                       'fem?kieli=sv': 'yle-fem-sv'}
        media_id = fem_mapping.get(channel, default_media_id)
        papiurl = 'http://papi.yle.fi/ng/live/rtmp/' + media_id + '/fin'
        return self.rtmp_parameters_from_papi(papiurl, url, True, filters)


### Areena live radio ###


class AreenaLiveRadioDownloader(object):
    @staticmethod
    def supported_protocols():
        return ['rtmp']

    def __init__(self, streaming_protocols):
        pass

    def download_episodes(self, url, filters, rtmpdumpargs, destdir):
        dl = self._downloader(url, filters, rtmpdumpargs, destdir)
        if not dl:
            return RD_FAILED

        return dl.save_stream()

    def _downloader(self, url, filters, rtmpdumpargs, destdir):
        streamurl = AreenaLiveRadioStreamUrl(url, filters)
        if not streamurl.is_valid():
            return None

        clip_title = self.get_live_stream_title(url)
        return streamurl.create_downloader(clip_title, destdir, rtmpdumpargs)

    def print_urls(self, url, print_episode_url, filters):
        printableurl = (url if print_episode_url
                        else AreenaLiveRadioStreamUrl(url, filters).to_url())
        enc = sys.getfilesystemencoding()
        print printableurl.encode(enc, 'replace')
        return RD_SUCCESS

    def pipe(self, url, filters):
        dl = self._downloader(url, filters, [], '')
        return dl.pipe()

    def print_titles(self, url, filters):
        enc = sys.getfilesystemencoding()
        print self.get_live_stream_title(url).encode(enc, 'replace')
        return RD_SUCCESS

    def get_live_stream_title(self, pageurl):
        m = re.match(r'http://(?:www\.)?yle\.fi/radio/([a-zA-Z0-9]+)/suora/?', pageurl)
        title = m.group(1) if m else 'yleradio'
        title += time.strftime('-%Y-%m-%d-%H:%M:%S')
        return title


class AreenaLiveRadioStreamUrl(AreenaRTMPStreamUrl):
    def __init__(self, pageurl, filters):
        AreenaRTMPStreamUrl.__init__(self, None, pageurl)
        self.rtmp_params = self._get_radio_rtmpurl(pageurl, filters)

    def _get_radio_rtmpurl(self, pageurl, filters):
        html = download_page(pageurl)
        if not html:
            return None

        radioid1 = re.search(r'"id": "/([0-9]+)"', html)
        radioid2 = re.search(r'id="live-channel".+data-id="([0-9]+)"', html)
        radioid = radioid1 or radioid2
        if not radioid:
            return None

        streamid = radioid.group(1)
        papiurl = 'http://papi.yle.fi/ng/radio/rtmp/%s/fi' % streamid
        return self.rtmp_parameters_from_papi(papiurl, pageurl, True, filters)


### Elava Arkisto ###


class ElavaArkistoDownloader(Areena2014Downloader):
    @staticmethod
    def supported_protocols():
        return ['hds', 'rtmp']

    def get_playlist(self, url, filters):
        dataids = self.get_dataids(url)
        playlist = [self.clip_from_dataid(d, url, filters) for d in dataids]
        if len(playlist) == 0:
            log(u"Can't find streams at %s." % url)
            return []

        if filters.latest_only:
            playlist = playlist[:1]

        if debug:
            log(u'playlist')
            log(str([p.streamurl.to_url() for p in playlist]))

        return playlist

    def get_dataids(self, url):
        page = download_page(url)
        if not page:
            return []

        return re.findall(r' data-id="([0-9-]+)"', page)

    def clip_from_dataid(self, dataid, pageurl, filters):
        mediaitem = self.load_jsonp(self.embed_url(dataid))
        if not mediaitem:
            return FailedClip(pageurl, 'Failed to download embeded media data')

        if debug:
            log(json.dumps(mediaitem))

        if mediaitem.get('status') == 404:
            return FailedClip(pageurl, mediaitem.get('message') or 'Failed with status 404')

        title = mediaitem.get('title') or \
                mediaitem.get('originalTitle') or \
                'elavaarkisto'
        download_url = mediaitem.get('downloadUrl')
        if download_url:
            return Clip(pageurl, title, HTTPStreamUrl(download_url), [])
        else:
            mediakanta_id = '6-' + mediaitem['mediakantaId']
            media_id = '26-' + mediaitem['id']
            proto = 'HDS' if self.backend.startswith('hds') else 'RTMPE'
            medias = self.yle_media_descriptor(mediakanta_id, media_id, proto)
            if not medias:
                return FailedClip(pageurl, 'Failed to parse media object')

            selected_media = self.select_media(medias, filters)

            return Clip(pageurl, title,
                        self.media_streamurl(selected_media, pageurl, filters),
                        self.media_subtitles(selected_media))

    def embed_url(self, dataid):
        if '-' in dataid:
            did = dataid.split('-')[-1]
        else:
            did = dataid

        return 'http://yle.fi/elavaarkisto/embed/%s.jsonp?callback=yleEmbed.eaJsonpCallback&instance=1&id=%s&lang=fi' % (did, did)

    def clip_for_url(self, clip, filters):
        return clip


### Svenska Arkivet ###


class ArkivetDownloader(Areena2014Downloader):
    @staticmethod
    def supported_protocols():
        return ['hds']

    def get_playlist(self, url, filters):
        return [url]

    def program_id_from_url(self, pageurl):
        dataids = self.get_dataids(pageurl)
        if dataids:
            return dataids[0]
        else:
            return None

    def get_dataids(self, url):
        page = download_page(url)
        if not page:
            return []

        dataids = re.findall(r' data-id="([0-9-]+)"', page)
        dataids = [d if '-' in d else '1-' + d for d in dataids]
        return dataids


### Downloader wrapper class that retries different protocols ###


class RetryingDownloader(object):
    def __init__(self, wrapped_class, protocols):
        self.wrapped_class = wrapped_class
        acceptable = wrapped_class.supported_protocols()
        is_supported = lambda p: acceptable.count(p.split(':', 1)[0]) > 0
        defaults = [p for p in DEFAULT_PROTOCOLS \
                    if any(p.startswith(a) for a in acceptable)]
        accepted, rejected = partition(is_supported, protocols or defaults)
        self.protocols = list(accepted)

        rejected = list(rejected)
        if rejected:
            log(u'The following protocols are not supported on this source: ' +
                u', '.join(rejected))

    def _create_next_downloader(self):
        if self.protocols:
            proto = self.protocols.pop(0)
            if debug:
                log('Streaming protocol %s' % proto)
            return self.wrapped_class(proto)
        else:
            return None

    def _retry_call(self, method_name, *args, **kwargs):
        downloader = self._create_next_downloader()
        if not downloader:
            return RD_FAILED

        method = getattr(downloader, method_name)
        res = method(*args, **kwargs)
        if res == RD_FAILED:
            return self._retry_call(method_name, *args, **kwargs)
        else:
            return res

    def print_urls(self, *args, **kwargs):
        return self._retry_call('print_urls', *args, **kwargs)

    def print_titles(self, *args, **kwargs):
        return self._retry_call('print_titles', *args, **kwargs)

    def download_episodes(self, *args, **kwargs):
        return self._retry_call('download_episodes', *args, **kwargs)

    def pipe(self, *args, **kwargs):
        return self._retry_call('pipe', *args, **kwargs)


### Download a stream to a local file ###


class BaseDownloader(object):
    def __init__(self, stream, clip_title, destdir, extra_argv):
        self.stream = stream
        self.clip_title = clip_title or 'ylestream'
        self.extra_argv = extra_argv or []
        self.destdir = destdir or ''
        self._cached_output_file = None

        if self.is_resume_job(extra_argv) and not self.resume_supported():
            log('Warning: Resume not supported on this stream')

    def save_stream(self):
        """Deriving classes override this to perform the download"""
        raise NotImplementedError('save_stream must be overridden')

    def pipe(self):
        """Derived classes can override this to pipe to stdout"""
        return RD_FAILED

    def outputfile_from_clip_title(self, ext='.flv', resume=False):
        if self._cached_output_file:
            return self._cached_output_file

        filename = self.sane_filename(self.clip_title) + ext
        if self.destdir:
            filename = os.path.join(self.destdir, filename)
        if not resume:
            filename = self.next_available_filename(filename)
        self._cached_output_file = filename
        return filename

    def next_available_filename(self, proposed):
        i = 1
        enc = sys.getfilesystemencoding()
        filename = proposed
        basename, ext = os.path.splitext(filename)
        while os.path.exists(filename.encode(enc, 'replace')):
            log(u'%s exists, trying an alternative name' % filename)
            filename = basename + '-' + str(i) + ext
            i += 1

        return filename

    def outputfile_from_args(self, args_in):
        if not args_in:
            return None

        prev = None
        args = list(args_in) # copy
        while args:
            opt = args.pop()
            if opt in ('-o', '--flv'):
                return prev
            prev = opt
        return None

    def log_output_file(self, outputfile, done=False):
        if outputfile and outputfile != '-':
            if done:
                log(u'Stream saved to ' + outputfile)
            else:
                log(u'Output file: ' + outputfile)

    def sane_filename(self, name):
        if isinstance(name, str):
            name = unicode(name, 'utf-8', 'ignore')
        tr = dict((ord(c), ord(u'_')) for c in excludechars)
        x = name.strip(' .').translate(tr)
        return x or u'ylevideo'

    def output_filename(self):
        return (self.outputfile_from_args(self.extra_argv) or
                self.outputfile_from_clip_title())

    def resume_supported(self):
        return False

    def is_resume_job(self, args):
        return '--resume' in args or '-e' in args


### Dumping a stream to a file using external programs ###


class ExternalDownloader(BaseDownloader):
    def save_stream(self):
        args = self.build_args()
        outputfile = self.outputfile_from_external_args(args)
        self.log_output_file(outputfile)
        retcode = self.external_downloader(args)
        if retcode == RD_SUCCESS:
            self.log_output_file(outputfile, True)
        return retcode

    def build_args(self):
        return []

    def outputfile_from_external_args(self, args):
        return self.outputfile_from_args(args)

    def external_downloader(self, args):
        """Start an external process such as rtmpdump with argument list args and
        wait until completion.
        """
        if debug:
            log('Executing:')
            log(' '.join(args))

        enc = sys.getfilesystemencoding()
        encoded_args = [x.encode(enc, 'replace') for x in args]

        try:
            if platform.system() == 'Windows':
                process = subprocess.Popen(encoded_args)
            else:
                process = subprocess.Popen(encoded_args,
                    preexec_fn=self._sigterm_when_parent_dies)
            return process.wait()
        except KeyboardInterrupt:
            try:
                os.kill(process.pid, signal.SIGINT)
                process.wait()
            except OSError:
                # The process died before we killed it.
                pass
            return RD_INCOMPLETE
        except OSError, exc:
            log(u'Failed to execute ' + ' '.join(args))
            log(unicode(exc.strerror, 'UTF-8', 'replace'))
            return RD_FAILED

    def _sigterm_when_parent_dies(self):
       PR_SET_PDEATHSIG = 1

       try:
           libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
       except AttributeError:
           # libc is None or libc does not contain prctl
           pass


### Download stream by delegating to rtmpdump ###


class RTMPDump(ExternalDownloader):
    def resume_supported(self):
        return True

    def build_args(self):
        args = [rtmpdump_binary]
        args += self.stream.to_rtmpdump_args()
        args += self._outputparams_unless_defined_in_extra_argv()
        args += self.extra_argv
        return args

    def output_filename(self):
        resume_job = self.is_resume_job(self.extra_argv)
        return (self.outputfile_from_args(self.extra_argv) or
                self.outputfile_from_clip_title(resume=resume_job))

    def _outputparams_unless_defined_in_extra_argv(self):
        if self.outputfile_from_args(self.extra_argv):
            return []
        else:
            return ['-o', self.output_filename()]

    def pipe(self):
        args = [rtmpdump_binary]
        args += self.stream.to_rtmpdump_args()
        args += ['-o', '-']
        self.external_downloader(args)
        return RD_SUCCESS


### Download a stream by delegating to AdobeHDS.php ###


class HDSDump(ExternalDownloader):
    def __init__(self, stream, clip_title, destdir, extra_argv, maxbitrate):
        ExternalDownloader.__init__(self, stream, clip_title, destdir, extra_argv)
        self.quality_options = self._bitrate_to_quality(maxbitrate)

    def _bitrate_to_quality(self, maxbitrate):
        # Approximate because there is no easy way to find out the
        # available bitrates in the HDS stream
        if maxbitrate < 1000:
            return ['--quality', 'low']
        elif maxbitrate < 2000:
            return ['--quality', 'medium']
        else:
            return []

    def build_args(self):
        args = list(hds_binary)
        args.append('--manifest')
        args.append(self.stream.to_url())
        args.append('--delete')
        args.append('--outfile')
        args.append(self.output_filename())
        args.extend(self.quality_options)
        if debug:
            args.append('--debug')
        return args

    def pipe(self):
        args = list(hds_binary)
        args.append('--manifest')
        args.append(self.stream.to_url())
        args.extend(self.quality_options)
        args.append('--play')
        if debug:
            args.append('--debug')
        self.external_downloader(args)
        self.cleanup_cookies()
        return RD_SUCCESS

    def outputfile_from_external_args(self, args_in):
        if not args_in:
            return None

        try:
            i = args_in.index('--outfile')
        except ValueError:
            i = -1

        if i >= 0 and i+1 < len(args_in):
            return args_in[i+1]
        else:
            return None

    def cleanup_cookies(self):
        try:
            os.remove('Cookies.txt')
        except OSError:
            pass


### Download a stream delegating to the youtube_dl HDS downloader ###


class YoutubeDLHDSDump(BaseDownloader):
    def __init__(self, stream, clip_title, destdir, extra_argv, maxbitrate):
        BaseDownloader.__init__(self, stream, clip_title, destdir, extra_argv)
        self.maxbitrate = maxbitrate

    def resume_supported(self):
        return True

    def save_stream(self):
        return self._execute_youtube_dl(self.output_filename())

    def pipe(self):
        return self._execute_youtube_dl(u'-')

    def _execute_youtube_dl(self, outputfile):
        try:
            import youtube_dl
        except ImportError:
            log(u'Failed to import youtube_dl')
            return RD_FAILED

        if outputfile != '-':
            self.log_output_file(outputfile)

        ydlopts = {
            'logtostderr': True,
            'verbose': debug
        }

        dlopts = {
            'nopart': True,
            'continuedl': outputfile != '-' and \
                self.is_resume_job(self.extra_argv)
        }

        ydl = youtube_dl.YoutubeDL(ydlopts)
        f4mdl = youtube_dl.downloader.F4mFD(ydl, dlopts)
        info = {'url': self.stream.to_url()}
        info.update(self._bitrate_parameter())
        try:
            if not f4mdl.download(outputfile, info):
                return RD_FAILED
        except urllib2.HTTPError, ex:
            log(u'HTTP request failed: %s %s' % (ex.code, ex.reason))
            return RD_FAILED

        if outputfile != '-':
            self.log_output_file(outputfile, True)
        return RD_SUCCESS

    def _stream_bitrates(self):
        manifest = download_page(self.stream.to_url())
        if not manifest:
            return []

        try:
            manifest_xml = xml.dom.minidom.parseString(manifest)
        except Exception as exc:
            log(unicode(exc.message, 'utf-8', 'ignore'))
            return []

        medias = manifest_xml.getElementsByTagName('media')
        bitrates = (int_or_else(m.getAttribute('bitrate'), 0) for m in medias)
        return [br for br in bitrates if br > 0]

    def _bitrate_parameter(self):
        bitrates = self._stream_bitrates()
        if debug:
            log(u'Available bitrates: %s, maxbitrate = %s' %
                (bitrates, self.maxbitrate))

        if not bitrates:
            return {}

        acceptable_bitrates = [br for br in bitrates if br <= self.maxbitrate]
        if not acceptable_bitrates:
            selected_bitrate = min(bitrates)
        else:
            selected_bitrate = max(acceptable_bitrates)

        if debug:
            log(u'Selected bitrate: %s' % selected_bitrate)

        return {'tbr': selected_bitrate}


### Download a plain HTTP file ###


class HTTPDump(BaseDownloader):
    def save_stream(self):
        log('Downloading from HTTP server...')
        if debug:
            log('URL: %s' % self.stream.to_url())
        filename = self.output_filename()
        self.log_output_file(filename)

        enc = sys.getfilesystemencoding()
        try:
            urllib.urlretrieve(self.stream.to_url(), filename.encode(enc))
        except IOError, exc:
            log(u'Download failed: ' + unicode(exc.message, 'UTF-8', 'replace'))
            return RD_FAILED

        self.log_output_file(filename, True)
        return RD_SUCCESS

    def output_filename(self):
        ext = self.stream.ext or '.flv'
        return (self.outputfile_from_args(self.extra_argv) or
                self.outputfile_from_clip_title(ext=ext))


### main program ###


def main():
    global debug
    global rtmpdump_binary
    global hds_binary
    latest_episode = False
    url_only = False
    title_only = False
    print_episode_url = False
    sublang = 'all'
    hardsubs = False
    bitratearg = sys.maxint
    show_usage = False
    url = None
    destdir = None
    streaming_protocols = None
    pipe = False

    # Is sys.getfilesystemencoding() the correct encoding for
    # sys.argv?
    encoding = sys.getfilesystemencoding()
    argv = [unicode(x, encoding, 'ignore') for x in sys.argv[1:]]
    rtmpdumpargs = []
    while argv:
        arg = argv.pop(0)
        if not arg.startswith('-'):
            url = arg
        elif arg in ['--verbose', '-V', '--debug', '-z']:
            debug = True
            rtmpdumpargs.append(arg)
        elif arg in ['--help', '-h']:
            show_usage = True
        elif arg in ['--latestepisode']:
            latest_episode = True
        elif arg == '--showurl':
            url_only = True
        elif arg == '--showtitle':
            title_only = True
        elif arg == '--showepisodepage':
            url_only = True
            print_episode_url = True
        elif arg == '--vfat':
            global excludechars
            global excludechars_windows
            excludechars = excludechars_windows
        elif arg == '--sublang':
            if argv:
                sublang = argv.pop(0)
        elif arg == '--hardsubs':
            hardsubs = True
        elif arg == '--maxbitrate':
            if argv:
                bitratearg = argv.pop(0)
        elif arg == '--rtmpdump':
            if argv:
                rtmpdump_binary = argv.pop(0)
        elif arg == '--adobehds':
            if argv:
                hds_binary = argv.pop(0).split(' ')
        elif arg == '--destdir':
            if argv:
                destdir = argv.pop(0)
        elif arg == '--protocol':
            if argv:
                streaming_protocols = argv.pop(0).split(',')
        elif arg == '--pipe':
            pipe = True
        elif arg == '-o':
            if argv:
                outputfile = argv.pop(0)
                rtmpdumpargs.extend([arg, outputfile])
                if outputfile == '-':
                    pipe = True
        else:
            rtmpdumpargs.append(arg)
            if arg in ARGOPTS and argv:
                rtmpdumpargs.append(argv.pop(0))

    if not rtmpdump_binary:
        if sys.platform == 'win32':
            rtmpdump_binary = which('rtmpdump.exe')
        else:
            rtmpdump_binary = which('rtmpdump')

    if show_usage or url is None:
        usage()
        sys.exit(RD_SUCCESS)

    if debug or not (url_only or title_only):
        splashscreen()

    if not rtmpdump_binary:
        log(u'Error: rtmpdump not found in path, use --rtmpdump for setting the location')
        sys.exit(RD_FAILED)

    url = encode_url_utf8(url)
    dl = downloader_factory(url, streaming_protocols)
    if not dl:
        log(u'Unsupported URL %s.' % url)
        log(u'Is this really a Yle video page?')
        sys.exit(RD_FAILED)

    maxbitrate = bitrate_from_arg(bitratearg)
    sfilt = StreamFilters(latest_episode, sublang, hardsubs, maxbitrate)
    if url_only:
        sys.exit(dl.print_urls(url, print_episode_url, sfilt))
    elif title_only:
        sys.exit(dl.print_titles(url, sfilt))
    elif pipe:
        sys.exit(dl.pipe(url, sfilt))
    else:
        sys.exit(dl.download_episodes(url, sfilt, rtmpdumpargs, destdir))


if __name__ == '__main__':
    main()
