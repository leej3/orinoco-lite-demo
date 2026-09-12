async function exchangeCode(code, config) {
    // Exchange code for token by POST request
    const body = new URLSearchParams({
        client_id: config.client_id,
        code: code,
        grant_type: config.grant_type,
        redirect_uri: config.redirect_uri,
        code_verifier: localStorage.getItem("pkce_verifier")
    });
    const res = await fetch(`${config.base_url}/${config.token_endpoint}`, {
        method: "POST",
        headers: {
            "Content-Type": "application/x-www-form-urlencoded"
        },
        body
    });
    const tokens = await res.json();
    return tokens;
}


async function main() {
    const params = new URLSearchParams(window.location.search)
    const code = params.get('code')

    if (!code) {
        document.body.textContent = "Invalid authentication callback.";
        return;
    }
    const state = params.get('state')
    if (state !== localStorage.getItem("oauth_state")) {
        throw new Error('Invalid OAuth state')
    }
    const config = JSON.parse(localStorage.getItem("oauth_config"));
    const tokens = await exchangeCode(code, config)
    window.opener?.postMessage(
        {
            type: "oidc-login-success",
            provider: config.name,
            payload: {
                accessToken: tokens.access_token,
                refreshToken: tokens.refresh_token,
                expiresIn: tokens.expires_in,
                tokenType: tokens.token_type
            }
        },
        window.location.origin
    );
    localStorage.removeItem("pkce_verifier");
    localStorage.removeItem("oauth_state");
    localStorage.removeItem("oauth_config");
    window.close();
}

main().catch(err => {
    console.error(err)
    document.body.textContent = 'Authentication failed.'
})