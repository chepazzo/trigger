# -*- coding: utf-8 -*-

from trigger.twister.channels import TriggerSSHChannelBase
from trigger.twister.core import TriggerDevicePlugin

NAME='netscaler'

class Plugin(TriggerDevicePlugin):
    def __init__(self):
        TriggerDevicePlugin.__init__(self,NAME)
        self.channel_class = TriggerSSHNetscalerChannel

class TriggerSSHNetscalerChannel(TriggerSSHChannelBase):
    """
    An SSH channel to interact with Citrix NetScaler hardware.

    It's almost a generic SSH channel except that we must check for errors
    first, because a prompt is not returned when an error is received. This had
    to be accounted for in the ``dataReceived()`` method.
    """
    def __init__(self):
        TriggerSSHChannelBase.__init__(self)

    def dataReceived(self, bytes):
        """Do this when we receive data."""
        self.data += bytes
        log.msg('[%s] BYTES: %r' % (self.device, bytes))
        #log.msg('BYTES: (left: %r, max: %r, bytes: %r, data: %r)' %
        #        (self.remoteWindowLeft, self.localMaxPacket, len(bytes), len(self.data)))

        # We have to check for errors first, because a prompt is not returned
        # when an error is received like on other systems.
        if has_netscaler_error(self.data):
            err = self.data
            if not self.with_errors:
                log.msg('[%s] Command failed: %r' % (self.device, err))
                self.factory.err = exceptions.CommandFailure(err)
                self.loseConnection()
                return None
            else:
                self.results.append(err)
                self._send_next()

        m = self.prompt.search(self.data)
        if not m:
            #log.msg('STATE: prompt match failure', debug=True)
            return None
        log.msg('[%s] STATE: prompt %r' % (self.device, m.group()))

        result = self.data[:m.start()] # Strip ' Done\n' from results.

        if self.initialized:
            self.results.append(result)

        if self.command_interval:
            log.msg('[%s] Waiting %s seconds before sending next command' %
                    (self.device, self.command_interval))
        reactor.callLater(self.command_interval, self._send_next)

