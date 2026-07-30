# Bootstrap ownership

This public repository owns only fresh-machine bootstrap and refresh of the
bootstrap/deployer checkouts. It may collect and protect deployment credentials,
install host prerequisites with consent, clone `robot-deploy`, and hand off an
explicit release request.

Runtime configuration, release selection/install, services, data, and updates
after handoff belong to private `robot-deploy`. Keep this repository Python
standard-library-only, never copy credentials into runtime configuration, and
test through root and command seams without sudo, package managers, or network.
