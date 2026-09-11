# CATALYST setup

## Confirmed choices

- Private source repository: https://github.com/mporosoff/CATALYST
- Initial modalities: reactor data, catalyst synthesis, and spectroscopy.
- Destination objects: propose after inspecting representative data and the SciSure tenant.
- Deployment platform: not selected or provisioned.

## Find the SciSure environment

The supplied developer guide describes access to the shared developer sandbox. It does not establish which production tenant or group this project uses.

1. Sign in to the [Developer portal](https://developer.elabnext.com/).
2. Select **Go to Sandbox**. The guide says the first visit activates the sandbox account through single sign-on.
3. Alternatively, open the [sandbox login page](https://sandbox.elabjournal.com/login/) and select **login using eLabNext Developer**.
4. After signing in, copy only the address origin: the `https://` part and hostname, without the remaining path or query string. For the developer sandbox, this is `https://sandbox.elabjournal.com`.
5. Note the active group name if visible. If no group information is apparent, supply the base URL first; group IDs can be inspected once a secure API connection is configured.

For an institutional production environment, use the same address-origin procedure on the normal SciSure login destination. A developer portal account alone does not prove access to that institutional tenant. Do not send login links containing temporary codes or tokens.

Source: [SciSure/eLabNext getting started](https://developer.elabnext.com/docs/getting-started), reviewed September 11, 2026.

## Representative data

Provide examples from at least 2–3 entities, labeled with the entity and modality. For each example, include the original CSV/XLSX/JSON file and any available column/unit definitions. Include corresponding processed results when they exist. Preserve representative units, identifiers, headers, and scientific context when preparing shareable examples.

Spectroscopy examples should identify the technique and instrument/export format if known. The specific schema and validation requirements depend on the technique.

These examples will determine the canonical extensions, source mapping profiles, initial processing scope, and proposed SciSure destinations. Real research files must not be committed to this repository.

## Configure the API secret

Once the backend deployment project exists:

1. In SciSure, open **Apps & Connections → Manage Authentication** to generate an API token if needed.
2. In the backend deployment platform, open its environment/secrets settings and add `SCISURE_API_TOKEN` as a secret.
3. Configure the verified base URL and allowed test group separately.
4. Confirm read access before enabling controlled publication tests.

Do not put the token in chat, GitHub source, a frontend build variable, browser storage, or a public configuration file. The deployment platform and exact secret-entry screen are still to be selected.

Source: [REST API overview](https://developer.elabnext.com/docs/overview), reviewed September 11, 2026.

## How the pasted SDK guide applies

The localhost HTTPS server, development certificates, and browser local-network settings in the pasted guide support add-on development inside SciSure. The planned external CATALYST web app uses the REST API through its backend. An in-SciSure add-on can be considered later; those add-on development steps are not part of the current repository setup.
