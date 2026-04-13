# Multi-user deployment checklist

Run the generated `apply_host.sh` as root, then validate each user below.

## alice

- [ ] Linux user `hmx_alice` exists
- [ ] `/home/hmx_alice/work` exists and is owned by `hmx_alice`
- [ ] `/home/hmx_alice/.hermes` exists and is owned by `hmx_alice`
- [ ] systemd unit `hermes-alice.service` is installed and active
- [ ] `curl -H "Authorization: Bearer replace-with-generated-key" http://127.0.0.1:8643/v1/models` succeeds
- [ ] Open WebUI connection is configured with `prefix_id=hermes-alice`
- [ ] Open WebUI base model resolves as `hermes-alice.Hermes`
- [ ] Wrapper model `hermes-alice` is imported with visible name `Hermes`
- [ ] Wrapper model `hermes-alice` is only granted to Open WebUI user `user_alice`
- [ ] Running `pwd` through Hermes lands in `/home/hmx_alice/work`

## bob

- [ ] Linux user `hmx_bob` exists
- [ ] `/home/hmx_bob/work` exists and is owned by `hmx_bob`
- [ ] `/home/hmx_bob/.hermes` exists and is owned by `hmx_bob`
- [ ] systemd unit `hermes-bob.service` is installed and active
- [ ] `curl -H "Authorization: Bearer replace-with-generated-key" http://127.0.0.1:8644/v1/models` succeeds
- [ ] Open WebUI connection is configured with `prefix_id=hermes-bob`
- [ ] Open WebUI base model resolves as `hermes-bob.Hermes`
- [ ] Wrapper model `hermes-bob` is imported with visible name `Hermes`
- [ ] Wrapper model `hermes-bob` is only granted to Open WebUI user `user_bob`
- [ ] Running `pwd` through Hermes lands in `/home/hmx_bob/work`
