OpenSSH_8.1p1, LibreSSL 2.7.3
debug1: Reading configuration data /Users/jacobr/.ssh/config
debug1: Reading configuration data /etc/ssh/ssh_config
debug1: /etc/ssh/ssh_config line 47: Applying options for *
debug1: Connecting to 54.225.135.143 [54.225.135.143] port 22.
debug1: Connection established.
debug1: identity file /Users/jacobr/.ssh/id_rsa type 0
debug1: identity file /Users/jacobr/.ssh/id_rsa-cert type -1
debug1: identity file /Users/jacobr/.ssh/id_dsa type -1
debug1: identity file /Users/jacobr/.ssh/id_dsa-cert type -1
debug1: identity file /Users/jacobr/.ssh/id_ecdsa type -1
debug1: identity file /Users/jacobr/.ssh/id_ecdsa-cert type -1
debug1: identity file /Users/jacobr/.ssh/id_ed25519 type -1
debug1: identity file /Users/jacobr/.ssh/id_ed25519-cert type -1
debug1: identity file /Users/jacobr/.ssh/id_xmss type -1
debug1: identity file /Users/jacobr/.ssh/id_xmss-cert type -1
debug1: Local version string SSH-2.0-OpenSSH_8.1
debug1: Remote protocol version 2.0, remote software version OpenSSH_7.4
debug1: match: OpenSSH_7.4 pat OpenSSH_7.0*,OpenSSH_7.1*,OpenSSH_7.2*,OpenSSH_7.3*,OpenSSH_7.4*,OpenSSH_7.5*,OpenSSH_7.6*,OpenSSH_7.7* compat 0x04000002
debug1: Authenticating to 54.225.135.143:22 as 'ec2-user'
debug1: SSH2_MSG_KEXINIT sent
debug1: SSH2_MSG_KEXINIT received
debug1: kex: algorithm: curve25519-sha256
debug1: kex: host key algorithm: ecdsa-sha2-nistp256
debug1: kex: server->client cipher: chacha20-poly1305@openssh.com MAC: <implicit> compression: none
debug1: kex: client->server cipher: chacha20-poly1305@openssh.com MAC: <implicit> compression: none
debug1: expecting SSH2_MSG_KEX_ECDH_REPLY
debug1: Server host key: ecdsa-sha2-nistp256 SHA256:tDdWbXwh91RI4ufYS+/bs5NlDknW+Fam8NFTnFMbG7Q
debug1: Host '54.225.135.143' is known and matches the ECDSA host key.
debug1: Found key in /Users/jacobr/.ssh/known_hosts:17
debug1: rekey out after 134217728 blocks
debug1: SSH2_MSG_NEWKEYS sent
debug1: expecting SSH2_MSG_NEWKEYS
debug1: SSH2_MSG_NEWKEYS received
debug1: rekey in after 134217728 blocks
debug1: Will attempt key: /Users/jacobr/.ssh/id_rsa RSA SHA256:s1mCOhNC84l554iB2uBswjYEuPkMQiSC1qfonGoRT1g
debug1: Will attempt key: /Users/jacobr/.ssh/id_dsa
debug1: Will attempt key: /Users/jacobr/.ssh/id_ecdsa
debug1: Will attempt key: /Users/jacobr/.ssh/id_ed25519
debug1: Will attempt key: /Users/jacobr/.ssh/id_xmss
debug1: SSH2_MSG_EXT_INFO received
debug1: kex_input_ext_info: server-sig-algs=<rsa-sha2-256,rsa-sha2-512>
debug1: SSH2_MSG_SERVICE_ACCEPT received
debug1: Authentications that can continue: publickey
debug1: Next authentication method: publickey
debug1: Offering public key: /Users/jacobr/.ssh/id_rsa RSA SHA256:s1mCOhNC84l554iB2uBswjYEuPkMQiSC1qfonGoRT1g
debug1: Server accepts key: /Users/jacobr/.ssh/id_rsa RSA SHA256:s1mCOhNC84l554iB2uBswjYEuPkMQiSC1qfonGoRT1g
debug1: Authentication succeeded (publickey).
Authenticated to 54.225.135.143 ([54.225.135.143]:22).
debug1: channel 0: new [client-session]
debug1: Requesting no-more-sessions@openssh.com
debug1: Entering interactive session.
debug1: pledge: network
debug1: client_input_global_request: rtype hostkeys-00@openssh.com want_reply 0
debug1: Sending environment.
debug1: Sending env LC_TERMINAL_VERSION = 3.3.7
debug1: Sending env LANG = en_US.UTF-8
debug1: Sending env LC_TERMINAL = iTerm2
Last login: Thu Nov 26 15:47:26 2020 from c-73-153-213-45.hsd1.co.comcast.net