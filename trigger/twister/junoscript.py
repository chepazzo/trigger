# -*- coding: utf-8 -*-

from trigger.twister.channels import TriggerSSHChannelBase
from trigger.twister.core import TriggerDevicePlugin
from xml.etree.ElementTree import (Element, ElementTree, XMLTreeBuilder,
                                   tostring)

NAME = 'junoscript'

class Plugin(TriggerDevicePlugin):
    def __init__(self):
        TriggerDevicePlugin.__init__(self,NAME)
        self.channel_class = TriggerSSHJunoscriptChannel

class TriggerSSHJunoscriptChannel(TriggerSSHChannelBase):
    """
    An SSH channel to execute Junoscript commands on a Juniper device running
    Junos.

    This completely assumes that we are the only channel in the factory (a
    TriggerJunoscriptFactory) and walks all the way back up to the factory for
    its arguments.
    """
    def __init__(self):
        TriggerSSHChannelBase.__init__(self,NAME)

    def channelOpen(self, data):
        """Do this when channel opens."""
        self._setup_channelOpen()
        self.conn.sendRequest(self, 'exec', common.NS('junoscript'))
        _xml = '<?xml version="1.0" encoding="us-ascii"?>\n'
        # TODO (jathan): Make the release version dynamic at some point
        _xml += '<junoscript version="1.0" hostname="%s" release="7.6R2.9">\n' % socket.getfqdn()
        self.write(_xml)
        self.xmltb = IncrementalXMLTreeBuilder(self._endhandler)

        self._send_next()

    def dataReceived(self, data):
        """Do this when we receive data."""
        log.msg('[%s] BYTES: %r' % (self.device, data))
        self.xmltb.feed(data)

    def _send_next(self):
        """Send the next command in the stack."""
        self.resetTimeout()

        if self.incremental:
            self.incremental(self.results)

        try:
            next_command = self.commanditer.next()
            log.msg('[%s] COMMAND: next command %s' % (self.device,
                                                       next_command))

        except StopIteration:
            log.msg('[%s] CHANNEL: out of commands, closing connection...' %
                    self.device)
            self.loseConnection()
            return None

        if next_command is None:
            self.results.append(None)
            self._send_next()
        else:
            rpc = Element('rpc')
            rpc.append(next_command)
            ElementTree(rpc).write(self)

    def _endhandler(self, tag):
        """Do this when the XML stream ends."""
        if tag.tag != '{http://xml.juniper.net/xnm/1.1/xnm}rpc-reply':
            return None # hopefully it's interior to an <rpc-reply>
        self.results.append(tag)

        if has_junoscript_error(tag) and not self.with_errors:
            log.msg('[%s] Command failed: %r' % (self.device, tag))
            self.factory.err = exceptions.JunoscriptCommandFailure(tag)
            self.loseConnection()
            return None

        # Honor the command_interval and then send the next command in the
        # stack
        else:
            if self.command_interval:
                log.msg('[%s] Waiting %s seconds before sending next command' %
                        (self.device, self.command_interval))
            reactor.callLater(self.command_interval, self._send_next)

#==================
# XML Stuff (for Junoscript)
#==================
class IncrementalXMLTreeBuilder(XMLTreeBuilder):
    """
    Version of XMLTreeBuilder that runs a callback on each tag.

    We need this because JunoScript treats the entire session as one XML
    document. IETF NETCONF fixes that.
    """
    def __init__(self, callback, *args, **kwargs):
        self._endhandler = callback
        XMLTreeBuilder.__init__(self, *args, **kwargs)

    def _end(self, tag):
        """Do this when we're out of XML!"""
        return self._endhandler(XMLTreeBuilder._end(self, tag))

