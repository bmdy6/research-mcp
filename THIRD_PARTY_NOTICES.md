# Third-party notices

The optional open-webSearch subprocess uses [Aas-ee/open-webSearch](https://github.com/Aas-ee/open-webSearch), version 2.2.0, pinned source commit `2562c9233a945c18fa9d08b7e450ca218a8853b6`, under Apache-2.0. Its license is preserved in [third_party/open-webSearch-LICENSE](third_party/open-webSearch-LICENSE). The search engine code is installed separately in a user cache; no upstream parser code is copied into the MIT Python package. The small JS bridge calls its existing runtime API. Upstream package-lock.json pins its transitive dependencies; those dependencies retain their own licenses in the separate installation.

Engine results remain subject to each source's data rights and terms. No SearXNG/AGPL code is incorporated.
