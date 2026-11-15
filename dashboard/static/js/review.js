// Hand review of the Shield-ON sessions on board #2 (Nov 14-15), read from the box's own log.
// The detector can't know what it missed; a person checked every unlisted name that got through.
// verdict: "confirmed" = known ad network; "suspected" = looks like ads, not verified.
// Keyed by the session id of the Shield-ON session the review was done on (board #2's digin.db).
export const REVIEW = {
  "5": {  // Block Blast round 3
    caught: {"bidease.com": ["confirmed", "Bidease ad network"],
             "startappservice.com": ["confirmed", "StartApp ad network"],
             "startappelb.com": ["confirmed", "StartApp load balancer"],
             "ibl-video.site": ["suspected", "BIGO IP range, likely BIGO Ads video; not verified"]},
    missed: [["aarki.net", "Aarki ad network; also used with Shield OFF, so not \"new\""]],
    unclear: [], full: true},
  "12": {  // Block Blast, later Shield-ON session
    caught: {"abr.ge": ["confirmed", "Airbridge ad-tracking link (checked by hand)"]},
    missed: [["aarki.net", "Aarki ad network got through again (also used with Shield OFF)"]],
    unclear: [], full: false},
  "7": {caught: {}, missed: [], unclear: [], full: true},   // Knife Hit
  "9": {caught: {}, missed: [],                              // Subway Surfers
    unclear: [["lazybumblebee.com", "unknown; also used with Shield OFF, not judged"]], full: true},
};
