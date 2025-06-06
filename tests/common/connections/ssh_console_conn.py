import time
import re
from .base_console_conn import CONSOLE_SSH_DIGI_CONFIG, BaseConsoleConn, CONSOLE_SSH
try:
    from netmiko.ssh_exception import NetMikoAuthenticationException
except ImportError:
    from netmiko.exceptions import NetMikoAuthenticationException
from paramiko.ssh_exception import SSHException


class SSHConsoleConn(BaseConsoleConn):
    def __init__(self, **kwargs):
        if "console_username" not in kwargs \
                or "console_password" not in kwargs:
            raise ValueError("Either console_username or console_password is not set")

        # Console via SSH connection need two groups of user/passwd
        self.sonic_username = kwargs['sonic_username']
        self.sonic_password = kwargs['sonic_password']

        # Store console type for later use
        self.console_type = kwargs['console_type']

        if self.console_type == CONSOLE_SSH:
            # Login requires port to be provided
            kwargs['username'] = kwargs['console_username'] + r':' + str(kwargs['console_port'])
            self.menu_port = None
        elif self.console_type.endswith("config"):
            # Login to config menu only requires username
            kwargs['username'] = kwargs['console_username']
        else:
            # Login requires menu port
            kwargs['username'] = kwargs['console_username']
            self.menu_port = kwargs['console_port']
        kwargs['password'] = kwargs['console_password']
        kwargs['host'] = kwargs['console_host']
        kwargs['device_type'] = "_ssh"
        super(SSHConsoleConn, self).__init__(**kwargs)

    def session_preparation(self):
        session_init_msg = self._test_channel_read()
        self.logger.debug(session_init_msg)

        if re.search(
            r"(Port is in use. Closing connection...|Cannot connect: line \[\d{2}\] is busy)",
            session_init_msg,
            flags=re.M
        ):
            console_port = self.username.split(':')[-1]
            raise PortInUseException(f"Host closed connection, as console port '{console_port}' is currently occupied.")

        if self.console_type.endswith("config"):
            # We can skip stage 2 login for config menu connections
            self.session_preparation_finalise()
            return

        if (self.menu_port):
            # For devices logining via menu port, 2 additional login are needed
            # Since we have attempted all passwords in __init__ of base class until successful login
            # So self.username and self.password must be the correct ones
            self.login_stage_2(username=self.username,
                               password=self.password,
                               menu_port=self.menu_port,
                               pri_prompt_terminator=r".*login")
        # Attempt all sonic password
        for i in range(0, len(self.sonic_password)):
            password = self.sonic_password[i]
            try:
                self.login_stage_2(username=self.sonic_username,
                                   password=password)
            except NetMikoAuthenticationException as e:
                if i == len(self.sonic_password) - 1:
                    raise e
            else:
                break

        self.session_preparation_finalise()

    def session_preparation_finalise(self):
        """
        Helper function to handle final stages of session preparation.
        """
        # Digi config menu has a unique prompt terminator (----->)
        if self.console_type == CONSOLE_SSH_DIGI_CONFIG:
            self.set_base_prompt(">")
        else:
            self.set_base_prompt()

        # Clear the read buffer
        time.sleep(0.3 * self.global_delay_factor)
        self.clear_buffer()

    def login_stage_2(self,
                      username,
                      password,
                      menu_port=None,
                      pri_prompt_terminator=r".*# ",
                      alt_prompt_terminator=r".*\$ ",
                      username_pattern=r"(?:user:|username|login|user name)",
                      pwd_pattern=r"assword",
                      delay_factor=1,
                      max_loops=20
                      ):
        """
        Perform a stage_2 login
        """
        delay_factor = self.select_delay_factor(delay_factor)
        time.sleep(1 * delay_factor)

        output = ""
        return_msg = ""
        i = 1
        menu_port_sent = False
        user_sent = False
        password_sent = False
        # The following prompt is only for SONiC
        # Need to add more login failure prompt for other system
        login_failure_prompt = r".*incorrect"
        while i <= max_loops:
            try:
                if menu_port and not menu_port_sent:
                    self.write_and_poll("menu ports", "Selection:")
                    self.write_channel(str(self.menu_port) + self.RETURN)
                    menu_port_sent = True

                output = self.read_channel()
                return_msg += output

                # Search for username pattern / send username
                if not user_sent and re.search(username_pattern, output, flags=re.I):
                    self.write_channel(username + self.RETURN)
                    time.sleep(1 * delay_factor)
                    output = self.read_channel()
                    return_msg += output
                    user_sent = True

                # Search for password pattern / send password
                if user_sent and not password_sent and re.search(pwd_pattern, output, flags=re.I):
                    self.write_channel(password + self.RETURN)
                    time.sleep(0.5 * delay_factor)
                    output = self.read_channel()
                    return_msg += output
                    password_sent = True
                    if re.search(
                            pri_prompt_terminator, output, flags=re.M
                    ) or re.search(alt_prompt_terminator, output, flags=re.M):
                        return return_msg

                # Check if proper data received
                if re.search(pri_prompt_terminator, output, flags=re.M) or re.search(
                        alt_prompt_terminator, output, flags=re.M
                ):
                    return return_msg

                # Check if login failed
                if re.search(login_failure_prompt, output, flags=re.M):
                    # Wait a short time or the next login will be refused
                    time.sleep(1 * delay_factor)
                    msg = "Login failed: {}".format(self.host)
                    raise NetMikoAuthenticationException(msg)

                self.write_channel(self.RETURN)
                time.sleep(0.5 * delay_factor)
                i += 1
            except EOFError:
                self.remote_conn.close()
                msg = "Login failed: {}".format(self.host)
                raise NetMikoAuthenticationException(msg)

        # Last try to see if we already logged in
        self.write_channel(self.RETURN)
        time.sleep(0.5 * delay_factor)
        output = self.read_channel()
        return_msg += output
        if re.search(pri_prompt_terminator, output, flags=re.M) or re.search(
                alt_prompt_terminator, output, flags=re.M
        ):
            return return_msg

        self.remote_conn.close()
        msg = "Login failed: {}".format(self.host)
        raise NetMikoAuthenticationException(msg)

    def cleanup(self):
        # If we are in SONiC, send an exit to logout
        if not self.console_type.endswith("config"):
            self.send_command(command_string="exit",
                              expect_string="login:")
        # remote_conn must be closed, or the SSH session will be kept on Digi,
        # and any other login is prevented
        self.remote_conn.close()
        del self.remote_conn


class PortInUseException(SSHException):
    '''Exception to denote a console port is in use.'''
    pass
