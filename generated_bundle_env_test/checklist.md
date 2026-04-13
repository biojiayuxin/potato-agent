# Multi-user deployment checklist

Run the generated `apply_host.sh` as root, then validate each user below.

## user_test

- [ ] Linux user `hmx_user_test` exists
- [ ] `/home/hmx_user_test/work` exists and is owned by `hmx_user_test`
- [ ] `/home/hmx_user_test/.hermes` exists and is owned by `hmx_user_test`
- [ ] systemd unit `hermes-user-test.service` is installed and active
- [ ] `curl -H "Authorization: Bearer test-shared-key" http://127.0.0.1:8643/v1/models` succeeds
- [ ] Open WebUI connection is configured with `prefix_id=hermes-user-test`
- [ ] Open WebUI base model resolves as `hermes-user-test.Hermes`
- [ ] Wrapper model `hermes-user-test` is imported with visible name `Hermes`
- [ ] Wrapper model `hermes-user-test` is only granted to Open WebUI user `342f9bf2-7cda-4408-8124-bff02a4f6ed7`
- [ ] Running `pwd` through Hermes lands in `/home/hmx_user_test/work`
