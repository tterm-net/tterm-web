"""The signer: holds the CA keys and signs short certificates, nothing else.

A separate process from day one. The core never sees a CA key: it sends a
public key and gets back a certificate that lives a couple of minutes. So
breaking into the core does not carry the keys away, and this process keeps
its own journal of every certificate it ever signed.

    python -m tterm.signer
"""
