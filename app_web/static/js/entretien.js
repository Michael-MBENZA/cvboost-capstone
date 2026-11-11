// Entretien vocal en temps réel avec la recruteuse IA (API « live » d'OpenAI, WebRTC).
//
//  1. Le navigateur prépare une connexion audio (micro + haut-parleur) et envoie son « offre SDP » au site.
//  2. Le site la transmet à OpenAI avec la consigne de la recruteuse (la clé reste sur le serveur)
//     et renvoie la réponse SDP : la connexion audio s'établit directement avec OpenAI.
//  3. La transcription arrive par petits morceaux sur un canal de données ; on les assemble en répliques
//     et on les sauvegarde sur le serveur toutes les 20 secondes. Elle n'est PAS affichée à l'écran
//     (comme dans un vrai entretien, on écoute sans lire) : elle sert seulement au bilan.
//  4. À 5 minutes (ou au clic sur « Terminer »), on coupe et on demande le bilan noté au site.
(function () {
    "use strict";

    var zone = document.getElementById("entretien");
    if (!zone) return;

    var boutonDemarrer = document.getElementById("demarrer");
    var boutonTerminer = document.getElementById("terminer");
    var boutonRecuperer = document.getElementById("recuperer-bilan");
    var etatTexte = document.getElementById("etat");
    var minuteur = document.getElementById("minuteur");
    var zoneErreur = document.getElementById("erreur");
    var zoneBilan = document.getElementById("bilan");
    var voix = document.getElementById("voix");
    var DUREE_MAX = parseInt(zone.dataset.dureeMax, 10);

    var etat = "attente";          // attente | connexion | direct | fin
    var connexion = null, micro = null, canal = null, chrono = null;
    var entretienId = null, secondes = 0, consigneOuverture = null;
    var echanges = [];             // [{ role: "recruteuse" | "candidat", texte }]
    var repliqueEnCours = null;    // { role, texte } : la phrase en train d'être prononcée

    // ---------- Outils ----------

    function envoyer(url, donnees) {
        return fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-CSRFToken": zone.dataset.csrf },
            body: JSON.stringify(donnees || {})
        }).then(function (r) {
            return r.json().catch(function () { return {}; }).then(function (json) {
                if (!r.ok) throw new Error(json.erreur || "Erreur du serveur.");
                return json;
            });
        });
    }

    function element(balise, texte, classe) {
        var el = document.createElement(balise);
        if (texte !== undefined && texte !== null) el.textContent = texte;
        if (classe) el.className = classe;
        return el;
    }

    function afficherErreur(message) {
        zoneErreur.hidden = !message;
        zoneErreur.textContent = message || "";
    }

    function afficherTemps() {
        var restant = Math.max(0, DUREE_MAX - secondes);
        var m = Math.floor(restant / 60), s = restant % 60;
        minuteur.textContent = m + ":" + (s < 10 ? "0" : "") + s;
    }

    function urlBilan(id) {
        return zone.dataset.bilan.replace(/\/0\/bilan$/, "/" + id + "/bilan");
    }

    function echangesPropres() {
        return echanges.filter(function (e) { return e.texte && e.texte.trim(); });
    }

    // ---------- Transcription ----------

    // L'API « live » envoie la transcription par petits MORCEAUX (« delta »), sans jamais signaler la fin
    // d'une phrase. On les assemble : tant que la même personne parle, on complète la même réplique ;
    // dès que l'autre personne parle, une nouvelle réplique commence.
    function ajouterMorceau(role, morceau) {
        if (!morceau) return;
        if (!repliqueEnCours || repliqueEnCours.role !== role) {
            repliqueEnCours = { role: role, texte: "" };
            echanges.push({ role: role, texte: "" });
        }
        repliqueEnCours.texte += morceau;
        echanges[echanges.length - 1].texte = repliqueEnCours.texte.trim();
    }

    function recevoir(message) {
        var evt;
        try { evt = JSON.parse(message.data); } catch (e) { return; }
        var type = evt.type || "";
        if (type === "session.started") {
            changerEtat("direct");
            // Amina parle la première : bonjour, présentation, puis elle laisse la parole au candidat
            if (consigneOuverture && canal && canal.readyState === "open") {
                canal.send(JSON.stringify({ type: "session.commentary.append", event_id: "ouverture",
                                            delegation_id: null, content: consigneOuverture }));
            }
        }
        if (type === "session.closed") arreter();
        if (type === "error") {
            console.warn("Erreur OpenAI :", evt.error);
            afficherErreur("Problème pendant l'entretien. Terminez puis relancez une session.");
            return;
        }
        var role = /input|user/.test(type) ? "candidat" : "recruteuse";
        if (/transcript.*delta/.test(type)) {
            // API « live » : morceaux de phrase (session.input_transcript.delta, session.output_transcript.delta)
            ajouterMorceau(role, evt.delta || evt.text || "");
        } else if (/transcript.*(completed|done)/.test(type)) {
            // Autres API temps réel : phrases complètes, chacune forme sa propre réplique
            var texte = evt.transcript || evt.text;
            if (typeof texte === "string" && texte.trim()) {
                repliqueEnCours = null;
                ajouterMorceau(role, texte.trim());
                repliqueEnCours = null;
            }
        }
    }

    function sauvegarder() {
        if (!entretienId) return;
        envoyer(urlBilan(entretienId), { echanges: echangesPropres(), duree: secondes, final: false })
            .catch(function () { /* on réessaiera à la prochaine sauvegarde */ });
    }

    // ---------- Déroulement ----------

    function changerEtat(nouvel) {
        etat = nouvel;
        clearInterval(chrono);
        if (etat === "direct") {
            etatTexte.textContent = "Entretien en cours : parlez naturellement, Amina vous écoute.";
            boutonTerminer.hidden = false;
            chrono = setInterval(function () {
                secondes++;
                afficherTemps();
                if (secondes % 20 === 0) sauvegarder();   // rien n'est perdu si la connexion coupe
                if (secondes >= DUREE_MAX) terminer();
            }, 1000);
        } else if (etat === "connexion") {
            etatTexte.textContent = "Connexion à la recruteuse…";
        }
    }

    function attendreAdressesReseau() {
        // On attend que le navigateur ait trouvé ses adresses réseau (3 s maximum)
        if (connexion.iceGatheringState === "complete") return Promise.resolve();
        var cette = connexion;
        return new Promise(function (fini) {
            var delai = setTimeout(fini, 3000);
            cette.addEventListener("icegatheringstatechange", function () {
                if (cette.iceGatheringState === "complete") { clearTimeout(delai); fini(); }
            });
        });
    }

    function demarrer() {
        afficherErreur("");
        zoneBilan.hidden = true;
        echanges = [];
        repliqueEnCours = null;
        secondes = 0;
        afficherTemps();
        boutonDemarrer.disabled = true;
        changerEtat("connexion");

        navigator.mediaDevices.getUserMedia({ audio: true }).then(function (flux) {
            micro = flux;
            connexion = new RTCPeerConnection();
            micro.getTracks().forEach(function (piste) { connexion.addTrack(piste, micro); });
            connexion.ontrack = function (e) {
                voix.srcObject = new MediaStream([e.track]);
                voix.play().catch(function () { afficherErreur("Activez le son du navigateur pour entendre Amina."); });
            };
            var cette = connexion;
            connexion.onconnectionstatechange = function () {
                if (cette.connectionState === "failed") arreter();
            };
            canal = connexion.createDataChannel("oai-events");
            canal.onmessage = recevoir;

            return connexion.createOffer()
                .then(function (offre) { return connexion.setLocalDescription(offre); })
                .then(attendreAdressesReseau)
                .then(function () { return envoyer(zone.dataset.session, { sdp: connexion.localDescription.sdp }); })
                .then(function (reponse) {
                    entretienId = reponse.entretien_id;
                    consigneOuverture = reponse.ouverture;
                    return connexion.setRemoteDescription({ type: "answer", sdp: reponse.sdp });
                });
            // C'est l'événement « session.started » qui lance le minuteur
        }).catch(function (e) {
            couper();
            changerEtat("attente");
            boutonDemarrer.disabled = false;
            etatTexte.textContent = "Prête quand vous l'êtes.";
            if (e && e.name === "NotAllowedError") afficherErreur("Accès au micro refusé : autorisez le micro (icône à gauche de l'adresse), puis réessayez.");
            else if (e && e.name === "NotFoundError") afficherErreur("Aucun micro détecté : branchez un micro ou un casque.");
            else afficherErreur(e && e.message ? e.message : "Connexion impossible.");
        });
    }

    function couper() {
        clearInterval(chrono);
        if (micro) micro.getTracks().forEach(function (p) { p.stop(); });
        if (canal) canal.close();
        if (connexion) connexion.close();
        voix.srcObject = null;
        micro = canal = connexion = null;
    }

    function terminer() {
        if (etat !== "direct") return;
        changerEtat("fin");
        etatTexte.textContent = "Fin de l'entretien…";
        if (canal && canal.readyState === "open") canal.send(JSON.stringify({ type: "session.close" }));
        setTimeout(arreter, 2500);   // laisse arriver les derniers morceaux de transcription
    }

    function arreter() {
        if (etat === "attente" && !entretienId) return;
        var avaitCommence = etat === "direct" || etat === "fin";
        couper();
        changerEtat("attente");
        boutonTerminer.hidden = true;
        boutonDemarrer.disabled = false;
        boutonDemarrer.textContent = "Recommencer un entretien";
        etatTexte.textContent = "Entretien terminé.";
        if (avaitCommence && entretienId) demanderBilan(entretienId, secondes);
        entretienId = null;
    }

    // ---------- Bilan ----------

    function demanderBilan(id, duree) {
        zoneBilan.hidden = false;
        zoneBilan.innerHTML = "";
        zoneBilan.appendChild(element("h2", "Bilan de l'entretien"));
        zoneBilan.appendChild(element("p", "Amina prépare votre bilan…", "discret"));
        zoneBilan.scrollIntoView({ behavior: "smooth" });
        envoyer(urlBilan(id), { echanges: echangesPropres(), duree: duree, final: true }).then(function (r) {
            afficherBilan(r.bilan);
        }).catch(function (erreur) {
            zoneBilan.innerHTML = "";
            zoneBilan.appendChild(element("h2", "Bilan de l'entretien"));
            zoneBilan.appendChild(element("p", erreur.message));
        });
    }

    function blocListe(titre, items) {
        var bloc = element("div");
        if (!items || !items.length) return bloc;
        bloc.appendChild(element("p", titre, "sous-titre"));
        var ul = element("ul", null, "liste");
        items.forEach(function (t) { ul.appendChild(element("li", t)); });
        bloc.appendChild(ul);
        return bloc;
    }

    function afficherBilan(b) {
        var niveau = b.note >= 70 ? "haut" : (b.note >= 40 ? "moyen" : "bas");
        zoneBilan.innerHTML = "";

        // Note, mention et message bref
        var grille = element("div", null, "synthese");
        var jauge = element("div", null, "jauge " + niveau);
        jauge.appendChild(element("strong", b.note + " / 100"));
        jauge.appendChild(document.createTextNode(b.mention || ""));
        var barre = element("div", null, "barre");
        var rempli = element("span");
        rempli.style.width = b.note + "%";
        barre.appendChild(rempli);
        jauge.appendChild(barre);

        var texte = element("div");
        texte.appendChild(element("h2", "Bilan de l'entretien"));
        if (b.message) texte.appendChild(element("p", b.message, "message-bref"));
        texte.appendChild(element("p", "Comment s'est passé l'entretien", "sous-titre"));
        texte.appendChild(element("p", b.deroulement));
        grille.appendChild(jauge);
        grille.appendChild(texte);
        zoneBilan.appendChild(grille);

        // Détail de la note : chaque critère, ses points et sa justification
        if (b.criteres && b.criteres.length) {
            zoneBilan.appendChild(element("p", "Détail de la note", "sous-titre"));
            var detail = element("ul", null, "criteres");
            b.criteres.forEach(function (c) {
                var li = element("li");
                var tete = element("div", null, "critere-tete");
                tete.appendChild(element("span", c.critere));
                tete.appendChild(element("strong", c.points + " / " + c.sur));
                li.appendChild(tete);
                var barreC = element("div", null, "barre");
                var remplie = element("span");
                remplie.style.width = Math.round(100 * c.points / c.sur) + "%";
                barreC.appendChild(remplie);
                li.appendChild(barreC);
                if (c.justification) li.appendChild(element("p", c.justification, "discret"));
                detail.appendChild(li);
            });
            zoneBilan.appendChild(detail);
        }

        // Points forts, points à améliorer, conseils
        var colonnes = element("div", null, "deux-colonnes");
        colonnes.appendChild(blocListe("Points forts", b.points_forts));
        colonnes.appendChild(blocListe("À améliorer", b.a_ameliorer));
        zoneBilan.appendChild(colonnes);
        zoneBilan.appendChild(blocListe("Pour vos prochains entretiens", b.conseils));
        zoneBilan.hidden = false;
        zoneBilan.scrollIntoView({ behavior: "smooth" });
    }

    // ---------- Boutons ----------

    if (boutonDemarrer) boutonDemarrer.addEventListener("click", demarrer);
    if (boutonTerminer) boutonTerminer.addEventListener("click", terminer);
    if (boutonRecuperer) {
        // Entretien précédent resté sans bilan (connexion coupée, page fermée...) : on peut le récupérer
        boutonRecuperer.addEventListener("click", function () {
            boutonRecuperer.disabled = true;
            demanderBilan(boutonRecuperer.dataset.id, parseInt(boutonRecuperer.dataset.duree || "0", 10));
        });
    }
    // Si la page est fermée en plein entretien, la dernière sauvegarde (moins de 20 s) reste sur le serveur :
    // le bilan pourra être récupéré en revenant sur la page.
    window.addEventListener("beforeunload", couper);
})();
