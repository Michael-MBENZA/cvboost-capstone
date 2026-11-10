// Pendant une analyse IA (environ 20 s), on désactive le bouton pour éviter un double clic
// (qui lancerait un deuxième appel payant) et on indique que le travail est en cours.
document.querySelectorAll("form.js-chargement").forEach(function (formulaire) {
    formulaire.addEventListener("submit", function () {
        var bouton = formulaire.querySelector("button[type=submit]");
        if (bouton) {
            bouton.disabled = true;
            bouton.textContent = "Analyse en cours… (environ 20 secondes)";
        }
    });
});
