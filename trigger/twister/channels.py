from twisted.conch.ssh import channel, common, session, transport, userauth
from twisted.protocols.policies import TimeoutMixin

#==================
# SSH Channels
#==================
class TriggerSSHChannelBase(channel.SSHChannel, TimeoutMixin, object):
    """
    Base class for SSH channels.

    The method self._setup_channelOpen() should be called by channelOpen() in
    the subclasses. Before you subclass, however, see if you can't just use
    TriggerSSHGenericChannel as-is!
    """
    name = 'session'

    def _setup_channelOpen(self):
        """
        Call me in your subclass in self.channelOpen()::

            def channelOpen(self, data):
                self._setup_channelOpen()
                self.conn.sendRequest(self, 'shell', '')
                # etc.
        """
        self.factory = self.conn.transport.factory
        self.commanditer = self.factory.commanditer
        self.results = self.factory.results
        self.with_errors = self.factory.with_errors
        self.incremental = self.factory.incremental
        self.command_interval = self.factory.command_interval
        self.prompt = self.factory.prompt
        self.setTimeout(self.factory.timeout)
        self.device = self.factory.device
        log.msg('[%s] COMMANDS: %r' % (self.device, self.factory.commands))
        self.data = ''
        self.initialized = self.factory.initialized
        self.startup_commands = copy.copy(self.device.startup_commands)
        log.msg('[%s] My startup commands: %r' % (self.device,
                                                  self.startup_commands))

        # For IOS-like devices that require 'enable'
        self.enable_prompt = re.compile(settings.IOSLIKE_ENABLE_PAT)
        self.enabled = False

    def channelOpen(self, data):
        """Do this when the channel opens."""
        self._setup_channelOpen()
        d = self.conn.sendRequest(self, 'shell', '', wantReply=True)
        d.addCallback(self._gotResponse)
        d.addErrback(self._ebShellOpen)

        # Don't call _send_next() here, since we (might) expect to see a
        # prompt, which will kick off initialization.

    def _gotResponse(self, response):
        """
        Potentially useful if you want to do something after the shell is
        initialized.

        If the shell never establishes, this won't be called.
        """
        log.msg('[%s] Got channel request response!' % self.device)

    def _ebShellOpen(self, reason):
        log.msg('[%s] Channel request failed: %s' % (self.device, reason))

    def dataReceived(self, bytes):
        """Do this when we receive data."""
        # Append to the data buffer
        self.data += bytes
        log.msg('[%s] BYTES: %r' % (self.device, bytes))
        #log.msg('BYTES: (left: %r, max: %r, bytes: %r, data: %r)' %
        #        (self.remoteWindowLeft, self.localMaxPacket, len(bytes),
        #         len(self.data)))

        # Keep going til you get a prompt match
        m = self.prompt.search(self.data)
        if not m:
            # Do we need to send an enable password?
            if not self.enabled and requires_enable(self, self.data):
                send_enable(self)
            return None

        log.msg('[%s] STATE: prompt %r' % (self.device, m.group()))

        # Strip the prompt from the match result
        result = self.data[:m.start()]
        result = result[result.find('\n')+1:]

        # Only keep the results once we've sent any startup_commands
        if self.initialized:
            self.results.append(result)

        # By default we're checking for IOS-like or Juniper errors because most
        # vendors # fall under this category.
        if (has_ioslike_error(result) or has_juniper_error(result)) and not self.with_errors:
            log.msg('[%s] Command failed: %r' % (self.device, result))
            self.factory.err = exceptions.CommandFailure(result)
            self.loseConnection()
            return None

        # Honor the command_interval and then send the next command
        else:
            if self.command_interval:
                log.msg('[%s] Waiting %s seconds before sending next command' %
                        (self.device, self.command_interval))
            reactor.callLater(self.command_interval, self._send_next)

    def _send_next(self):
        """Send the next command in the stack."""
        # Reset the timeout and the buffer for each new command
        self.data = ''
        self.resetTimeout()

        if not self.initialized:
            log.msg('[%s] Not initialized; sending startup commands' %
                    self.device)
            if self.startup_commands:
                next_init = self.startup_commands.pop(0)
                log.msg('[%s] Sending initialize command: %r' % (self.device,
                                                                 next_init))
                self.write(next_init.strip() + self.device.delimiter)
                return None
            else:
                log.msg('[%s] Successfully initialized for command execution' %
                        self.device)
                self.initialized = True

        if self.incremental:
            self.incremental(self.results)

        try:
            next_command = self.commanditer.next()
        except StopIteration:
            log.msg('[%s] CHANNEL: out of commands, closing connection...' %
                    self.device)
            self.loseConnection()
            return None

        if next_command is None:
            self.results.append(None)
            self._send_next()
        else:
            log.msg('[%s] Sending SSH command %r' % (self.device,
                                                     next_command))
            self.write(next_command + self.device.delimiter)

    def loseConnection(self):
        """
        Terminate the connection. Link this to the transport method of the same
        name.
        """
        log.msg('[%s] Forcefully closing transport connection' % self.device)
        self.conn.transport.loseConnection()

    def timeoutConnection(self):
        """
        Do this when the connection times out.
        """
        log.msg('[%s] Timed out while sending commands' % self.device)
        self.factory.err = exceptions.CommandTimeout('Timed out while sending commands')
        self.loseConnection()

    def request_exit_status(self, data):
        status = struct.unpack('>L', data)[0]
        log.msg('[%s] Exit status: %s' % (self.device, status))

class TriggerSSHGenericChannel(TriggerSSHChannelBase):
    """
    An SSH channel using all of the Trigger defaults to interact with network
    devices that implement SSH without any tricks.

    Currently A10, Cisco, Brocade, NetScreen can simply use this. Nice!

    Before you create your own subclass, see if you can't use me as-is!
    """

class TriggerSSHAsyncPtyChannel(TriggerSSHChannelBase):
    """
    An SSH channel that requests a non-interactive pty intended for async
    usage.

    Some devices won't allow a shell without a pty, so we have to do a
    'pty-req'.

    This is distinctly different from ~trigger.twister.TriggerSSHPtyChannel`
    which is intended for interactive end-user sessions.
    """
    def channelOpen(self, data):
        self._setup_channelOpen()

        # Request a pty even tho we are not actually using one.
        pr = session.packRequest_pty_req(os.environ['TERM'], (80, 24, 0, 0), '')
        self.conn.sendRequest(self, 'pty-req', pr)
        d = self.conn.sendRequest(self, 'shell', '', wantReply=True)
        d.addCallback(self._gotResponse)
        d.addErrback(self._ebShellOpen)

class TriggerSSHCommandChannel(TriggerSSHChannelBase):
    """
    Run SSH commands on a system using 'exec'

    This will multiplex channels over a single connection. Because of the
    nature of the multiplexing setup, the master list of commands is stored on
    the SSH connection, and the state of each command is stored within each
    individual channel which feeds its result back to the factory.
    """
    def __init__(self, command, *args, **kwargs):
        super(TriggerSSHCommandChannel, self).__init__(*args, **kwargs)
        self.command = command
        self.result = None
        self.data = ''

    def channelOpen(self, data):
        """Do this when the channel opens."""
        self._setup_channelOpen()
        log.msg('[%s] Channel was opened' % self.device)
        d = self.conn.sendRequest(self, 'exec', common.NS(self.command),
                                  wantReply=True)
        d.addCallback(self._gotResponse)
        d.addErrback(self._ebShellOpen)

    def _gotResponse(self, _):
        """
        If the shell never establishes, this won't be called.
        """
        log.msg('[%s] CHANNEL %s: Exec finished.' % (self.device, self.id))
        self.conn.sendEOF(self)

    def _ebShellOpen(self, reason):
        log.msg('[%s] CHANNEL %s: Channel request failed: %s' % (self.device,
                                                                 reason,
                                                                 self.id))

    def dataReceived(self, bytes):
        self.data += bytes
        #log.msg('BYTES INFO: (left: %r, max: %r, bytes: %r, data: %r)' %
        #        (self.remoteWindowLeft, self.localMaxPacket, len(bytes), len(self.data)))
        log.msg('[%s] BYTES RECV: %r' % (self.device, bytes))

    def eofReceived(self):
        log.msg('[%s] CHANNEL %s: EOF received.' % (self.device, self.id))
        result = self.data

        # By default we're checking for IOS-like errors because most vendors
        # fall under this category.
        if has_ioslike_error(result) and not self.with_errors:
            log.msg('[%s] Command failed: %r' % (self.device, result))
            self.factory.err = exceptions.CommandFailure(result)

        # Honor the command_interval and then send the next command
        else:
            self.result = result
            self.conn.transport.factory.results.append(self.result)
            self.send_next_command()

    def send_next_command(self):
        """Send the next command in the stack stored on the connection"""
        log.msg('[%s] CHANNEL %s: sending next command!' % (self.device, self.id))
        self.conn.send_command()

    def closeReceived(self):
        log.msg('[%s] CHANNEL %s: Close received.' % (self.device, self.id))
        self.loseConnection()

    def loseConnection(self):
        """Default loseConnection"""
        log.msg("[%s] LOSING CHANNEL CONNECTION" % self.device)
        channel.SSHChannel.loseConnection(self)

    def closed(self):
        log.msg('[%s] Channel %s closed' % (self.device, self.id))
        log.msg('[%s] CONN CHANNELS: %s' % (self.device,
                                            len(self.conn.channels)))

        # If we're out of channels, shut it down!
        if len(self.conn.transport.factory.results) == len(self.conn.commands):
            log.msg('[%s] RESULTS MATCHES COMMANDS SENT.' % self.device)
            self.conn.transport.loseConnection()

    def request_exit_status(self, data):
        exitStatus = int(struct.unpack('>L', data)[0])
        log.msg('[%s] Exit status: %s' % (self.device, exitStatus))


#==================
# Telnet Channels
#==================

class TriggerTelnet(telnet.Telnet, telnet.ProtocolTransportMixin, TimeoutMixin):
    """
    Telnet-based session login state machine. Primarily used by IOS-like type
    devices.
    """
    def __init__(self, timeout=settings.TELNET_TIMEOUT):
        self.protocol = telnet.TelnetProtocol()
        self.waiting_for = [
            ('Username: ', self.state_username),                  # Most
            ('Please Enter Login Name  : ', self.state_username), # OLD Foundry
            ('User Name:', self.state_username),                  # Dell
            ('login: ', self.state_username),                     # Arista, Juniper
            ('Password: ', self.state_login_pw),
        ]
        self.data = ''
        self.applicationDataReceived = self.login_state_machine
        self.timeout = timeout
        self.setTimeout(self.timeout)
        telnet.Telnet.__init__(self)

    def enableRemote(self, option):
        """
        Allow telnet clients to enable options if for some reason they aren't
        enabled already (e.g. ECHO). (Ref: http://bit.ly/wkFZFg) For some reason
        Arista Networks hardware is the only vendor that needs this method
        right now.
        """
        #log.msg('[%s] enableRemote option: %r' % (self.host, option))
        log.msg('enableRemote option: %r' % option)
        return True

    def login_state_machine(self, bytes):
        """Track user login state."""
        self.host = self.transport.connector.host
        log.msg('[%s] CONNECTOR HOST: %s' % (self.host,
                                             self.transport.connector.host))
        self.data += bytes
        log.msg('[%s] STATE:  got data %r' % (self.host, self.data))
        for (text, next_state) in self.waiting_for:
            log.msg('[%s] STATE:  possible matches %r' % (self.host, text))
            if self.data.endswith(text):
                log.msg('[%s] Entering state %r' % (self.host,
                                                    next_state.__name__))
                self.resetTimeout()
                next_state()
                self.data = ''
                break

    def state_username(self):
        """After we've gotten username, check for password prompt."""
        self.write(self.factory.creds.username + '\n')
        self.waiting_for = [
            ('Password: ', self.state_password),
            ('Password:', self.state_password),  # Dell
        ]

    def state_password(self):
        """After we got password prompt, check for enabled prompt."""
        self.write(self.factory.creds.password + '\n')
        self.waiting_for = [
            ('#', self.state_logged_in),
            ('>', self.state_enable),
            ('> ', self.state_logged_in),             # Juniper
            ('\n% ', self.state_percent_error),
            ('# ', self.state_logged_in),             # Dell
            ('\nUsername: ', self.state_raise_error), # Cisco
            ('\nlogin: ', self.state_raise_error),    # Arista, Juniper
        ]

    def state_logged_in(self):
        """
        Once we're logged in, exit state machine and pass control to the
        action.
        """
        self.setTimeout(None)
        data = self.data.lstrip('\n')
        log.msg('[%s] state_logged_in, DATA: %r' % (self.host, data))
        del self.waiting_for, self.data

        # Run init_commands
        self.factory._init_commands(protocol=self) # We are the protocol

        # Control passed here :)
        action = self.factory.action
        action.transport = self
        self.applicationDataReceived = action.dataReceived
        self.connectionLost = action.connectionLost
        action.write = self.write
        action.loseConnection = self.loseConnection
        action.connectionMade()
        action.dataReceived(data)

    def state_enable(self):
        """
        Special Foundry breakage because they don't do auto-enable from
        TACACS by default. Use 'aaa authentication login privilege-mode'.
        Also, why no space after the Password: prompt here?
        """
        log.msg("[%s] ENABLE: Sending command: enable" % self.host)
        self.write('enable\n')
        self.waiting_for = [
            ('Password: ', self.state_enable_pw), # Foundry
            ('Password:', self.state_enable_pw),  # Dell
        ]

    def state_login_pw(self):
        """Pass the login password from the factory or NetDevices"""
        if self.factory.loginpw:
            pw = self.factory.loginpw
        else:
            from trigger.netdevices import NetDevices
            pw = NetDevices().find(self.host).loginPW

        # Workaround to avoid TypeError when concatenating 'NoneType' and
        # 'str'. This *should* result in a LoginFailure.
        if pw is None:
            pw = ''

        #log.msg('Sending password %s' % pw)
        self.write(pw + '\n')
        self.waiting_for = [('>', self.state_enable),
                            ('#', self.state_logged_in),
                            ('\n% ', self.state_percent_error),
                            ('incorrect password.', self.state_raise_error)]

    def state_enable_pw(self):
        """Pass the enable password from the factory or NetDevices"""
        if self.factory.enablepw:
            pw = self.factory.enablepw
        else:
            from trigger.netdevices import NetDevices
            pw = NetDevices().find(self.host).enablePW
        #log.msg('Sending password %s' % pw)
        self.write(pw + '\n')
        self.waiting_for = [('#', self.state_logged_in),
                            ('\n% ', self.state_percent_error),
                            ('incorrect password.', self.state_raise_error)]

    def state_percent_error(self):
        """
        Found a % error message. Don't return immediately because we
        don't have the error text yet.
        """
        self.waiting_for = [('\n', self.state_raise_error)]

    def state_raise_error(self):
        """Do this when we get a login failure."""
        self.waiting_for = []
        log.msg('Failed logging into %s' % self.transport.connector.host)
        self.factory.err = exceptions.LoginFailure('%r' % self.data.rstrip())
        self.loseConnection()

    def timeoutConnection(self):
        """Do this when we timeout logging in."""
        log.msg('[%s] Timed out while logging in' % self.transport.connector.host)
        self.factory.err = exceptions.LoginTimeout('Timed out while logging in')
        self.loseConnection()


