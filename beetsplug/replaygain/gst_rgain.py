#!/usr/bin/env python
# -*- coding: utf-8 -*-

# pygain - Python Replay Gain analysis
# Copyright 2013 Dave Hayes <dwhayes@gmail.com>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301, USA.

# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4

# Code comments? We don't need no stinking code comments!


import os
import pygst
pygst.require('0.10')
import gst
import gobject
gobject.threads_init()
from Queue import Queue, Empty
import time


EXTENSIONS = (
    'mp3',
    'mp2',
    'mp4',
    'm4a',
    'ogg',
    'oga',
    'wma',
    'asf',
    'flac'
    )


class Track(gobject.GObject):
    __gsignals__ = {
        'test-complete': (
            gobject.SIGNAL_RUN_LAST,
            gobject.TYPE_NONE,
            (gobject.TYPE_PYOBJECT,)
        )
    }

    def __init__(self, path):
        super(Track, self).__init__()
        self.gain = None
        self.peak = None
        self.name = os.path.split(path)[1]
        self.path = os.path.abspath(path)
        self.source = gst.element_factory_make('filesrc', self.name)
        self.source.set_property('location', self.path)

    def test(self):
        self.test_queue = Queue()
        self.test_pipeline = gst.parse_launch('decodebin2 name="test-decbin-%s" ! audioconvert name="test-conv-%s" ! fakesink' % (self.name, self.name))
        self.decbin = self.test_pipeline.get_by_name('test-decbin-%s' % self.name)
        self.decbin.connect('autoplug-sort', self._on_autoplug_sort)
        self.decbin.connect('pad-added', self._on_pad_added)
        self.decbin.connect('pad-removed', self._on_pad_removed)
        self.conv = self.test_pipeline.get_by_name('test-conv-%s' % self.name)
        test_bus =  self.test_pipeline.get_bus()
        test_bus.add_signal_watch()
        test_bus.connect('message', self._on_message)
        self.test_src = gst.element_factory_make('filesrc', 'test-%s' % self.name)
        self.test_src.set_property('location', self.path)
        self.test_pipeline.add(self.test_src)
        self.test_src.link(self.decbin)
        self.test_pipeline.set_state(gst.STATE_PLAYING)

    def _on_autoplug_sort(self, decbin, pad, caps, factories):
        choices = []
        suggested = False
        for factory in factories:
            if suggested:
                choices.append(factory)
                continue
            name = factory.get_name()
            if name in ('mad', 'flump3dec'):
                try:
                    choices.append(gst.element_factory_find('ffdec_mp3float'))
                except:
                    pass
            choices.append(factory)
        if choices:
            return choices

    def _on_pad_added(self, decbin, pad):
        try:
            decbin.link(self.conv)
        except gst.LinkError:
            pass

    def _on_pad_removed(self, decbin, pad):
        decbin.unlink(self.conv)

    def _on_message(self, bus, msg):
        if msg.type == gst.MESSAGE_ERROR:
            self.test_pipeline.set_state(gst.STATE_NULL)
            self.emit('test-complete', False)
        elif msg.type == gst.MESSAGE_STATE_CHANGED:
            if not msg.src == self.test_pipeline:
                return
            old, new, pending = msg.parse_state_changed()
            if new == gst.STATE_PLAYING:
                self.test_pipeline.set_state(gst.STATE_NULL)
                self.emit('test-complete', True)
            

class Album(gobject.GObject):
    __gsignals__ = {
        'finished': (
            gobject.SIGNAL_RUN_LAST,
            gobject.TYPE_NONE,
            (gobject.TYPE_PYOBJECT,)
        )
    }

    def __init__(self, *args):
        if not args:
            return None
        super(Album, self).__init__()
        self.error = False
        self.gain = None
        self.peak = None
        self.tracks = Queue()
        for path in args:
            extension = os.path.splitext(path)[1][1:]
            if extension.lower() not in EXTENSIONS:
                continue
            self.tracks.put(Track(path))
        self.current_track = None
        self.results = []
        elements = (
            'decodebin2 name="decbin"',
            'audioconvert name="conv"',
            'audioresample',
            'rganalysis name="rg"',
            'fakesink')
        self.pipeline = gst.parse_launch(' ! '.join(elements))
        self.decbin = self.pipeline.get_by_name('decbin')
        self.decbin.connect('autoplug-sort', self._on_autoplug_sort)
        self.decbin.connect('pad-added', self._on_pad_added)
        self.decbin.connect('pad-removed', self._on_pad_removed)
        self.bus =  self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.bus.connect('message', self._on_message)
        self.rg = self.pipeline.get_by_name('rg')
        self.rg.set_property('num-tracks', self.tracks.qsize())
        self.conv = self.pipeline.get_by_name('conv')
        self.loop = gobject.MainLoop()
        self._next_track(False)
        
    def _next_track(self, unlink=True):
        if self.current_track:
            self.results.append({
                'gain': self.current_track.gain,
                'peak': self.current_track.peak
                })
            #print '%0.2f dB, %0.6f' % (self.current_track.gain, self.current_track.peak)
        if unlink:
            self.current_track.source.unlink(self.decbin)
            self.pipeline.remove(self.current_track.source)
        try:
            self.current_track = self.tracks.get(False)
            #print self.current_track.name,
            self.current_track.connect('test-complete', self._on_test_complete)
            self.current_track.test()

        except Empty:
            self.results.append({
                'gain': self.gain,
                'peak': self.peak
                })
            print 'Album: %0.2f dB, %0.6f' % (self.gain, self.peak)
            self.emit('finished', self)
            self.loop.quit()

    def _on_test_complete(self, track, valid):
        if valid:
            self.pipeline.add(self.current_track.source)
            self.current_track.source.link(self.decbin)
            self.rg.set_locked_state(False)
            self.pipeline.set_state(gst.STATE_PLAYING)
        else:
            self.rg.set_property('num-tracks', self.tracks.qsize())
            self._next_track(False)

    def _on_autoplug_sort(self, decbin, pad, caps, factories):
        choices = []
        suggested = False
        for factory in factories:
            if suggested:
                choices.append(factory)
                continue
            name = factory.get_name()
            if name in ('mad', 'flump3dec'):
                try:
                    choices.append(gst.element_factory_find('ffdec_mp3float'))
                except:
                    pass
            choices.append(factory)
        if choices:
            return choices

    def _on_pad_added(self, decbin, pad):
        try:
            decbin.link(self.conv)
        except gst.LinkError:
            pass

    def _on_pad_removed(self, decbin, pad):
        decbin.unlink(self.conv)

    def _on_message(self, bus, msg):
        if msg.type == gst.MESSAGE_TAG:
            if not msg.src == self.rg:
                return
            tags = msg.parse_tag()
            for tag in tags.keys():
                if tag == gst.TAG_TRACK_GAIN:
                    self.current_track.gain = round(tags[tag], 2)
                elif tag == gst.TAG_TRACK_PEAK:
                    self.current_track.peak = round(tags[tag], 6)
                elif tag == gst.TAG_ALBUM_GAIN:
                    self.gain = round(tags[tag], 2)
                elif tag == gst.TAG_ALBUM_PEAK:
                    self.peak = round(tags[tag], 6)
        elif msg.type in (gst.MESSAGE_EOS, gst.MESSAGE_ERROR):
            self.rg.set_locked_state(True)
            self.pipeline.set_state(gst.STATE_NULL)
            self._next_track()            

def compute_rgain(*args):
    album = Album(*args)
    album.loop.run()
    results = album.results
    del album
    return results