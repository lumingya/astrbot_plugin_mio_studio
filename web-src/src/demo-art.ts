// Original offline illustration plates. Drawn in SVG for crisp covers and zoom.
const grain = `
  <pattern id="p" width="11" height="11" patternUnits="userSpaceOnUse">
    <path d="M0 1h1m6 4h1m-4 4h1m5 2h1" stroke="#473734" opacity=".22"/>
  </pattern>
  <pattern id="water" width="31" height="13" patternUnits="userSpaceOnUse">
    <path d="M2 4h10m7 6h11" stroke="#e0bc98" opacity=".45"/>
  </pattern>`;

const stars = Array.from({ length: 52 }, (_, index) => {
  const x = (index * 83 + 27) % 680 + 20;
  const y = (index * index * 17 + index * 11) % 200 + 20;
  return `<circle cx="${x}" cy="${y}" r="${index % 7 === 0 ? 1.7 : 0.8}"/>`;
}).join("");

const starscape = `
  <rect width="720" height="460" fill="#202b3c"/>
  <circle cx="543" cy="117" r="69" fill="#daaa80"/>
  <circle cx="568" cy="92" r="67" fill="#202b3c"/>
  <g fill="#ede0c7">${stars}</g>
  <path d="M0 262 77 225l39 9 74-54 81 51 34-21 69 38 90-54 125 52 60-35 71 24v225H0z" fill="#555765"/>
  <path d="M0 295 79 252l45 4 37-23 70 49 35-18 62 25 99-50 62 33 46-16 44 25 83-22 58 31v170H0z" fill="#3a4e56"/>
  <path d="M0 338q124-32 260-6 90 18 214 6t246-30v152H0z" fill="#53656c"/>
  <path d="M0 338q124-32 260-6 90 18 214 6t246-30v152H0z" fill="url(#water)"/>
  <path d="M0 407 720 336v15L0 429z" fill="#263744"/>
  <path d="M0 406 720 335m0 15L0 428m83-9v41m77-51v51m87-63v63m84-76v76m90-86v86m93-100v100m90-112v112" fill="none" stroke="#232b32" stroke-width="2"/>
  <g stroke="#302725" stroke-width="2">
    <path d="M143 367l4-31 13-12 128-12 12 11 1 35z" fill="#a36451"/>
    <path d="M305 358l1-35 9-11 122-11 16 12 2 34z" fill="#b67b5a"/>
    <path d="m458 347 2-33 20-13 78-8 16 11 2 31z" fill="#a36451"/>
    <path d="m158 338 109-10 11 15-123 13zm159-15 118-11v15l-118 12zm158-10 78-8 11 12-92 10z" fill="#e5bd80"/>
    <path d="m173 338-2 16m19-18-1 16m19-18-1 16m21-18-1 15m22-18-1 16m91-24v15m21-18v15m22-18v15m24-17v14m87-18-1 13m21-17-1 14m22-17-1 13" fill="none"/>
  </g>
  <g fill="#29353c"><circle cx="180" cy="364" r="7"/><circle cx="274" cy="355" r="7"/><circle cx="332" cy="354" r="7"/><circle cx="433" cy="344" r="7"/><circle cx="490" cy="342" r="7"/><circle cx="561" cy="335" r="7"/></g>
  <path d="M47 407v-93m-19 17 43-8m541 33v-83m-18 19 37-8" stroke="#232e36" stroke-width="4"/>
  <path d="M36 323q90-20 205-24l380-16m-561 43 543-38" stroke="#3c4146" fill="none"/>
  <path d="M553 388q75-9 138-23m-128 34q50-7 119-20m-64 23 74-15" stroke="#d9a979" opacity=".45" fill="none"/>
`;

const shoreline = `
  <rect width="720" height="460" fill="#e7bb93"/>
  <path d="M0 114h720m-690 29h157m297-77h183m-476 20h168" stroke="#f1d6af" stroke-width="2"/>
  <circle cx="439" cy="134" r="64" fill="#c97457"/>
  <path d="M0 194 48 163l53 19 79-48 80 43 67-27 72 33 78-22 39 19 74-37 130 46v271H0z" fill="#af8878"/>
  <path d="M0 234 76 198l70 24 83-29 63 35 142 7 56-36 71 19 78-27 81 27v242H0z" fill="#767d78"/>
  <path d="M0 264q184-17 341-6t379-7v209H0z" fill="#628184"/>
  <path d="M0 254q170 12 271 8 125-5 301-13l148 2" stroke="#d0a883" fill="none" stroke-width="2"/>
  <path d="M0 268h720v192H0z" fill="url(#water)" opacity=".8"/>
  <path d="M0 331q72-15 156 26 79 37 195 48 64 6 151 55H0z" fill="#495b58"/>
  <path d="M0 340q74-11 149 27 85 44 204 49 47 2 128 44H0z" fill="#c89876"/>
  <path d="M0 353q81-8 150 25t153 39" stroke="#e0bba0" fill="none" stroke-width="2"/>
  <g stroke="#413b35" stroke-width="2">
    <path d="M313 353h109l-22 11h-68z" fill="#634838"/>
    <path d="M365 341v-68" fill="none"/>
    <path d="M363 279v59h-41z" fill="#f5dec3"/>
    <path d="M368 291v47h29z" fill="#dfb49a"/>
  </g>
  <path d="m416 324 86-1m-95 8 93-1m-80 12 91-2m-75 13 56-2m-34 9 69-1" stroke="#e0ad83" stroke-width="1.5" opacity=".7"/>
  <g fill="#524640"><path d="m97 299 8-31 15-8 13 9 8 33z"/><path d="m118 268 4-20h13l5 10-12 14z"/><path d="m95 300-7 27h5l13-23 13-2 11 27h6l-7-29z"/></g>
  <path d="m89 330 3-18m37 20 4-17m-71 34 74-4" stroke="#524640" stroke-width="1.5"/>
  <path d="M542 104q7-8 15 0 8-8 15 0m-3 14q6-5 12 0 5-5 11 0" fill="none" stroke="#7a5146" stroke-width="2"/>
`;

const greenhouse = `
  <rect width="720" height="460" fill="#dcbe96"/>
  <circle cx="458" cy="141" r="95" fill="#e7c899"/>
  <path d="M0 343V69l135-39 62 170 49 79 90 17 61-20 77 44 70-33 55-6 121 22v157H0z" fill="#8c9174"/>
  <path d="M0 330 101 285l103 25 82-22 99 31 69-25 111 27 91-36 64 26v149H0z" fill="#647662"/>
  <path d="M58 379 124 199 246 110l110 21 114 119v156zm412-129 114-38 83 62v120l-197 12z" fill="#b8a887" stroke="#374738" stroke-width="4"/>
  <path d="m124 199 122-89 110 21 114 119m-302-83 113-33 94 104m-169-94 102-15 122 112m-306-42 346 51m-366 3 366 44m-382 6 382 34m-397 8 397 28m-346-172-46 178m100-204-44 210m103-237-35 245m101-226-24 230m87-208-9 214m75-183 9 187m103-168 3 164m64-190 4 184m60-151v143m-177-121 197-23m-197 58 197-20m-197 58 197-18" fill="none" stroke="#485645" opacity=".9" stroke-width="2.7"/>
  <path d="M0 410q103-39 219-13 85 21 151 8t350-16v71H0z" fill="#ceae8e"/>
  <path d="M0 451q100-40 224-21 101 17 218 10t278-38v58H0z" fill="#677762"/>
  <path d="M264 399q20-56 38-70m-31 49q-6-24-22-29m32 16q20-20 42-17m-49 25q-2-30 4-39m-16 63q-37-26-42-48m64 13q10-33 28-44m-40 66q44-13 54-29" fill="none" stroke="#384f43" stroke-width="3"/>
  <g fill="#384f43"><ellipse cx="243" cy="347" rx="14" ry="4" transform="rotate(30 243 347)"/><ellipse cx="300" cy="336" rx="14" ry="4" transform="rotate(-42 300 336)"/><ellipse cx="307" cy="355" rx="13" ry="4" transform="rotate(-20 307 355)"/><ellipse cx="246" cy="380" rx="16" ry="4" transform="rotate(22 246 380)"/><ellipse cx="286" cy="379" rx="16" ry="4" transform="rotate(-30 286 379)"/></g>
  <path d="M0 280q36 79 52 180m-24-104q-10-56 50-115m-39 155q53-62 83-58m-74 81q45-33 82-24m585-128q-60 83-69 192m23-72q-41-61-87-70m87 59q18-80 44-109m-54 139q-37-30-77-25" fill="none" stroke="#435745" stroke-width="6"/>
  <path d="M0 286q32 20 41 79Q7 348 0 316zm76-41q17 34-42 114 5-72 42-114zm51 91q-31 10-83 55 43-5 83-55zm7 58q-52-8-85 32 51 0 85-32zm456-79q29 3 75 64-48-8-75-64zm125-56q-15 57-42 119-7-57 42-119zm-134 113q42-19 82 35-35-3-82-35z" fill="#435745"/>
`;

const city = `
  <rect width="720" height="460" fill="#b1a59f"/>
  <path d="M0 154h720m-626 34h181m269-57h78m-301-7h80" stroke="#d7bab2" stroke-width="3"/>
  <circle cx="520" cy="165" r="77" fill="#d6aea0"/>
  <path d="M0 212 127 175l90 43 81-29 69 32 84-13 65 9 64-18 140 19v242H0z" fill="#796d72"/>
  <path d="M0 290V68l167 44v167zM720 43l-167 61v187L720 307z" fill="#694e4e"/>
  <path d="M11 73v212m42-200v201m40-191v192m36-181v179m38-173v179m553-241-164 59m164 3-164 44m164 0-164 34m164 9-164 21m164 13-164 18" fill="none" stroke="#3c353a" stroke-width="2"/>
  <path d="M0 209v251h178V253l-61-37zM720 192l-158 49v219h158z" fill="#5a4546"/>
  <path d="M178 279 296 261l153 7 113 20v172H178z" fill="#9c8274"/>
  <path d="M178 305 298 272l153 1 112 31 157 86v70H0v-61z" fill="#ad8f83"/>
  <path d="M244 302 82 460m224-178-36 178m100-182 38 182m12-178 192 178m-459-94 441 4m-486 42 533 2" stroke="#6e5557" stroke-width="2" fill="none" opacity=".75"/>
  <path d="M100 266h43v67h-43zm-62-20h36v83H38zm547 25 25-8v71l-25 6zm46-19 55-22v105l-55 12z" fill="#cea573"/>
  <path d="M118 266v67m-18-44h43m-89-43v83m-16-57h36m520-2v66m-9-38 25-9m51-48v100m-30-68 55-18m-55 40 55-17" stroke="#735550" stroke-width="3"/>
  <g fill="#735650" stroke="#503e40" stroke-width="2">
    <path d="M275 301v-24l10-10 13 10v27zm132 4v-28l9-9 12 11v29z"/>
    <path d="M337 272q0-66 35-66t34 68h-69z"/>
    <path d="M348 273q-1-56 23-56t24 56z" fill="#c39777"/>
  </g>
  <ellipse cx="365" cy="354" rx="35" ry="6" fill="#735a58" opacity=".4"/>
  <path d="M355 324q-6-35 6-51 10-12 18 7l6 48-17 14z" fill="#393d45"/>
  <path d="m364 329-5 24h5l7-21 7 23h5l-4-29z" fill="#373741"/>
  <path d="M362 275q7-5 17-1l-1 8-7 4-9-3z" fill="#d4ac8e"/>
  <path d="M334 274q8-24 39-28 32 0 38 23l-30-4-11 4-19-1z" fill="#8c554e" stroke="#57414a" stroke-width="2"/>
  <path d="M373 247v51q1 8 7 6" fill="none" stroke="#503e40" stroke-width="2"/>
  <path d="M48 85l-17 27m61-7-15 24m91 12-19 31m79-19-11 20m28 48-16 29m76-90-16 30m89-78-21 35m86 30-14 28m125-111-18 35m22 96-16 29m110-125-18 39m-42 177-13 28m68-57-13 28m-537-32-21 38m62-37-16 29m285-3-18 37m57-51-13 27m-245 41-15 33m163-35-16 34" stroke="#dfc0b0" stroke-width="1" opacity=".3"/>
`;

const plates = [starscape, shoreline, greenhouse, city];

export function plate(index: number): string {
  const page = (index % plates.length + plates.length) % plates.length;
  const source = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 460">
  <defs>${grain}</defs>${plates[page]}
  <rect width="720" height="460" fill="url(#p)" opacity=".75"/>
</svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(source)}`;
}
