# Tipoff catalog

The rewards data for the Tipoff iOS app: which credit cards earn what, where, and the statement
credits attached to them. The app downloads this file so card terms can be corrected without
shipping an app update.

**This repository contains data and the privacy policy only. No application code.**

```
v1/catalog.json          the catalog the app fetches
v1/catalog.schema.json   JSON Schema describing its shape
privacy.html             the app's privacy policy, published for App Store Connect
```

### What's in it

| | |
|---|---|
| Cards | 116 US consumer credit cards |
| Merchants | 213 chains with their category codes |
| Statement credits | 106 across 41 cards |
| Points programs | 28 |

### Accuracy

Card terms are hand-checked against issuer pages and dated per card in `verifiedAt`. Terms change
often, so entries carry a source URL and anything confirmed only from a secondary source says so in
its notes. Treat it as a careful editorial record, not an authoritative one: always check your own
issuer's current terms before relying on a number.

Nothing here is personal data. The app sends nothing when it fetches this file.
