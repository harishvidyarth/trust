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
2. The browser warns about the certificate. This is expected because the certificate is made on the server laptop. Choose Advanced, then Continue.
3. Open the API address once with /healthz on the end, for example https://192.168.1.20:8000/healthz and accept the warning there too.
4. Go back to the Console and sign in.

## Why it uses HTTPS

Browsers block the camera and microphone on plain http addresses, except on localhost. The identity check needs both, so LAN mode always uses HTTPS and secure cookies.

## Things to know

- A new certificate is made when the Wi-Fi address changes and lasts 7 days. It is kept in .state/tls and is never committed.
- Sign in is required in LAN mode. Anyone on the Wi-Fi can reach the login page, so use strong passwords and turn off sign up with FIREWALL_ALLOW_SIGNUP=0 once everyone has an account.
- This is for a lab or demo network. Do not use it on public Wi-Fi.
- Stop with Ctrl+C.
