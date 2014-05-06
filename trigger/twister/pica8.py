# -*- coding: utf-8 -*-

from trigger.twister.channels import TriggerSSHAsyncPtyChannel
from trigger.twister.core import TriggerDevicePlugin
from trigger.conf import settings

NAME = 'pica8'
VENDOR = 'pica8'
NO_MORE_COMMANDS = ['show']
PROMPT_PATTERNS = r'\S+(?:\>|#)\s?$'
VENDOR_MAP = 'PICA8'
SUPPORTED_PLATFORMS = ['ROUTER', 'SWITCH']
DEFAULT_TYPES = 'SWITCH'

class Plugin(TriggerDevicePlugin):
    def __init__(self):
        TriggerDevicePlugin.__init__(self,NAME)
        self.no_more_commands = NO_MORE_COMMANDS
        self.channel_class = TriggerSSHPica8Channel
        # Still need to update settings.SUPPORTED_VENDORS tuple
        settings.PROMPT_PATTERNS[VENDOR] = PROMPT_PATTERNS
        settings.VENDOR_MAP[VENDOR_MAP] = VENDOR
        settings.SUPPORTED_PLATFORMS[VENDOR] = SUPPORTED_PLATFORMS
        settings.DEFAULT_TYPES[VENDOR] = DEFAULT_TYPES

class TriggerSSHPica8Channel(TriggerSSHAsyncPtyChannel):

    def _setup_commanditer(self, commands=None):
        """
        Munge our list of commands and overload self.commanditer to append
        " | no-more" to any "show" commands.
        """
        if commands is None:
            commands = self.factory.commands
        new_commands = []
        for command in commands:
            root = command.split(' ', 1)[0] # get the root command
            if root in PICA8_NO_MORE_COMMANDS:
                command += ' | no-more'
            new_commands.append(command)
        self.commanditer = iter(new_commands)

    def channelOpen(self, data):
        """
        Override channel open, which is where commanditer is setup in the
        base class.
        """
        super(TriggerSSHPica8Channel, self).channelOpen(data)
        self._setup_commanditer() # Replace self.commanditer with our version

