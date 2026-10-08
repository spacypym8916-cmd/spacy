# PlaylistCut website

One-page English site for [playlistcut.shop](https://playlistcut.shop).

- `index.html`: the whole site (no build step)
- `CNAME`: custom domain for GitHub Pages

## Publish with GitHub Pages

1. Settings > Pages > Build and deployment: Deploy from a branch, pick the branch and `/ (root)`.
2. Custom domain: `playlistcut.shop`, then turn on Enforce HTTPS once it is available.
3. At your DNS provider, add A records for `@`: `185.199.108.153`, `185.199.109.153`, `185.199.110.153`, `185.199.111.153`, and a CNAME for `www` pointing to `<github-username>.github.io`.
