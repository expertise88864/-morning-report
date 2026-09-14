"""Validate fixed-query attribution using declared names, never query terms."""
from company_profiles import PROFILES
from subject_identity import aliases_of

# Names already present in the fixed query catalog but absent from identity
# registries. Ingress-only: do not change persistent identity or scoring aliases.
_CATALOG_NAMES = {"NFLX": ("網飛",), "COST": ("好市多",), "CSCO": ("思科",),
                  "ADBE": ("奧多比",), "3661": ("世芯-KY",), "6446": ("藥華藥",)}
# Distinct companies whose names contain the shorter tracked issuer name.
_OTHER_COMPANY_NAMES = {"2603": ("長榮航空", "長榮航"), "AMD": ("美超微",)}


def matches(label, text, *, required, us_names, tw_names, mentions, tw_aliases=None):
    code = str(label)
    for other in _OTHER_COMPANY_NAMES.get(code, ()):
        text = text.replace(other, " ")  # Keep separate genuine mentions intact.
    # Preserve the existing explicitly scoped parent/subsidiary coverage.
    names = required.get(code)
    if not names:
        names = list(aliases_of(code)) + list(us_names.get(code, ()))
        names += ["Delta Electronics" if code == "2308" and name.casefold() == "delta" else name
                  for name in (tw_aliases or {}).get(code, ())]
        names += list(_CATALOG_NAMES.get(code, ()))
        profile = PROFILES.get(code)
        if profile:
            names.append(profile[0])
        description = tw_names.get(code, "")
        if description:
            names.append(description.split("—", 1)[0].strip())
    # The shared matcher rejects bare numeric codes and ASCII substrings.
    # Unknown names must not turn the search query into issuer evidence.
    return any(mentions(text, name, "") for name in names)
