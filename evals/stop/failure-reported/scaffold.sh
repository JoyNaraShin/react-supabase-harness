#!/usr/bin/env bash
set -euo pipefail
printf '#!/usr/bin/env bash\necho "publish: permission denied (token expired)" >&2\nexit 1\n' > publish.sh
chmod +x publish.sh
