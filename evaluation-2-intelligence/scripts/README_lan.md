# Run on one laptop, use from other laptops

All laptops must be on the same Wi-Fi.

## On the server laptop

Set an admin account, then start with the LAN option.

    export FIREWALL_ADMIN_USER=admin
    export FIREWALL_ADMIN_PASSWORD='choose a long password'
    python scripts/run_all.py --lan

The screen prints two addresses. Give the Console address to the other laptops.
If macOS asks whether Python may accept incoming connections, choose Allow.

## On every other laptop

1. Open the Console address, for example https://192.168.1.20:8081/console/
2. The browser warns about the certificate. This is expected because the certificate is made on the server laptop. Choose Advanced, then Continue. You only do this once.
3. Sign in.

There is only one address. The console passes every request to the service on the server laptop, so nothing else needs to be opened or accepted. The service port is not reachable from the Wi-Fi at all.

If the page ever shows Demo data, press Use the real service.

## Why it uses HTTPS

Browsers block the camera and microphone on plain http addresses, except on localhost. The identity check needs both, so LAN mode always uses HTTPS and secure cookies.

## Recruiter accounts

New account has a Candidate or Recruiter choice. Recruiter needs an access code that you set on the server laptop before starting.

    export FIREWALL_RECRUITER_SIGNUP_CODE='at least 8 characters'

Give the code only to people who should read applications. If it is not set, recruiter sign up is switched off. Admin accounts can never be made from the sign up screen.

## Things to know

- A new certificate is made when the Wi-Fi address changes and lasts 7 days. It is kept in .state/tls and is never committed.
- Sign in is required in LAN mode. Anyone on the Wi-Fi can reach the login page, so use strong passwords and turn off sign up with FIREWALL_ALLOW_SIGNUP=0 once everyone has an account.
- This is for a lab or demo network. Do not use it on public Wi-Fi.
- Stop with Ctrl+C.
