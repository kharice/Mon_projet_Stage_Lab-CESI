// Elements principaux de l'interface.
const canvas = document.getElementById('drawCanvas');
const ctx = canvas.getContext('2d');
const status = document.getElementById('status');

// Parametres de la grille de dessin.
const CELL_SIZE = 80;
const COLS = 7;
const ROWS = 5;
const REAL_CELL_CM = 25;

// Etat du trace et du monitoring.
let isDrawing = false;
let points = [];
let currentStroke = [];
let statusInterval = null;
let isAlertOpen = false; // Sécurité anti-spam pour l'alert() en cas de rafraîchissement asynchrone

// Position du robot au centre du canvas.
const centerX = Math.floor(COLS / 2) * CELL_SIZE;
const centerY = Math.floor(ROWS / 2) * CELL_SIZE;

// -------------------------------------------------------------------------
// DESSIN DU CANEVAS
// -------------------------------------------------------------------------
function drawGrid() {
    // Efface toute la zone avant de redessiner.
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.strokeStyle = '#1e3a5f';
    ctx.lineWidth = 1;

    // Colonnes verticales.
    for (let x = 0; x <= COLS; x++) {
        ctx.beginPath();
        ctx.moveTo(x * CELL_SIZE, 0);
        ctx.lineTo(x * CELL_SIZE, canvas.height);
        ctx.stroke();
    }

    // Lignes horizontales.
    for (let y = 0; y <= ROWS; y++) {
        ctx.beginPath();
        ctx.moveTo(0, y * CELL_SIZE);
        ctx.lineTo(canvas.width, y * CELL_SIZE);
        ctx.stroke();
    }

    // Marqueur visuel du robot.
    ctx.fillStyle = '#e94560';
    ctx.beginPath();
    ctx.arc(centerX, centerY, 15, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = '#1a1a2e';
    ctx.font = 'bold 12px Segoe UI';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('BOT', centerX, centerY);
}

// Initialisation au chargement du script
drawGrid();

function getPos(e) {
    // Conversion coordonnees ecran -> coordonnees canvas (support zoom/layout).
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    // Gestion tactile et souris avec la meme sortie {x, y}.
    if (e.touches) {
        return {
            x: (e.touches[0].clientX - rect.left) * scaleX,
            y: (e.touches[0].clientY - rect.top) * scaleY
        };
    } else {
        return {
            x: (e.clientX - rect.left) * scaleX,
            y: (e.clientY - rect.top) * scaleY
        };
    }
}

// -------------------------------------------------------------------------
// ÉVÉNEMENTS DE TRACÉ (SOURIS & TACTILE)
// -------------------------------------------------------------------------
function startDraw(e) {
    // Demarre un nouveau segment libre.
    e.preventDefault();
    isDrawing = true;
    currentStroke = [];
    const pos = getPos(e);
    ctx.strokeStyle = '#e94560';
    ctx.lineWidth = 3;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.beginPath();
    ctx.moveTo(pos.x, pos.y);
    currentStroke.push(pos);
}

function draw(e) {
    // Continue le trace tant que le pointeur est appuye.
    e.preventDefault();
    if (!isDrawing) return;
    const pos = getPos(e);
    ctx.lineTo(pos.x, pos.y);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(pos.x, pos.y);
    currentStroke.push(pos);
}

function stopDraw(e) {
    // Termine le segment courant et fusionne les points accumules.
    if (!isDrawing) return;
    isDrawing = false;
    points = points.concat(currentStroke);
    status.className = '';
    status.textContent = points.length + ' points enregistrés';
}

canvas.addEventListener('mousedown', startDraw);
canvas.addEventListener('mousemove', draw);
canvas.addEventListener('mouseup', stopDraw);
canvas.addEventListener('mouseleave', stopDraw);
canvas.addEventListener('touchstart', startDraw);
canvas.addEventListener('touchmove', draw);
canvas.addEventListener('touchend', stopDraw);

// -------------------------------------------------------------------------
// LOGIQUE DE SURVEILLANCE DU STATUT (Avec alerte Pop-up tablette)
// -------------------------------------------------------------------------
function startStatusMonitoring() {
    // Sécurité : On nettoie l'ancien intervalle s'il y en a un pour éviter d'accélérer les requêtes
    if (statusInterval) {
        clearInterval(statusInterval);
        statusInterval = null;
    }

    statusInterval = setInterval(async () => {
        try {
            const res = await fetch('/robot_status');
            const d = await res.json();

            // Etat bloque: on coupe le polling et on avertit l'operateur.
            if (d.status === 'blocked') {
                status.className = 'error';
                status.textContent = "Robot bloqué ! Venez l'aider manuellement.";
                
                // ARRÊT IMMÉDIAT DE LA BOUCLE DE VÉRIFICATION
                clearInterval(statusInterval);
                statusInterval = null;

                // Affichage unique de la fenêtre pop-up d'avertissement
                if (!isAlertOpen) {
                    isAlertOpen = true;
                    setTimeout(() => {
                        alert("🚨 ALERTE ROBOTIQUE :\n\nLe robot est bloqué et n'a trouvé aucun échappatoire valide.\n\nDéplacez-le manuellement et appuyez sur 'Reprendre'.");
                        isAlertOpen = false;
                    }, 100);
                }

            // Etat idle: execution terminee, on arrete aussi le polling.
            } else if (d.status === 'idle') {
                status.className = 'success';
                status.textContent = 'Trajectoire terminée !';
                clearInterval(statusInterval);
                statusInterval = null;
            }
        } catch (err) {
            // Sur erreur reseau, on stoppe la boucle pour eviter le spam d'erreurs.
            console.error("Erreur lors de la récupération du statut du robot:", err);
            clearInterval(statusInterval);
            statusInterval = null;
        }
    }, 1000); // Une vérification toutes les secondes
}

// -------------------------------------------------------------------------
// ACTIONS DES BOUTONS INTERFACE
// -------------------------------------------------------------------------
document.getElementById('btn-clear').addEventListener('click', () => {
    // Reinitialise le trace local et l'affichage.
    points = [];
    drawGrid();
    status.className = '';
    status.textContent = 'Commence ton tracé depuis le centre';
    if (statusInterval) {
        clearInterval(statusInterval);
        statusInterval = null;
    }
});

document.getElementById('btn-send').addEventListener('click', async () => {
    // Validation minimale pour eviter l'envoi de traces trop courtes.
    if (points.length < 5) {
        status.className = 'error';
        status.textContent = "Trace quelque chose d'abord !";
        return;
    }

    if (statusInterval) {
        clearInterval(statusInterval);
        statusInterval = null;
    }

    status.className = 'sending';
    status.textContent = 'Envoi au robot...';

    try {
        // Envoi de la trajectoire au backend Flask.
        const response = await fetch('/send_trajectory', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ points: points })
        });
        const data = await response.json();

        if (data.status === 'ok') {
            status.className = 'success';
            status.textContent = 'Trajectoire envoyée ! Le robot est en route.';
            
            // Lancement du monitoring du statut
            startStatusMonitoring();
        } else {
            status.className = 'error';
            status.textContent = 'Erreur : ' + data.message;
        }
    } catch (err) {
        status.className = 'error';
        status.textContent = 'Impossible de contacter le serveur Flask.';
    }
});

document.getElementById('btn-stop').addEventListener('click', async () => {
    // Force l'arret du robot cote serveur.
    if (statusInterval) {
        clearInterval(statusInterval);
        statusInterval = null;
    }
    status.className = 'error';
    status.textContent = 'Arrêt du robot...';
    try {
        await fetch('/stop_robot', { method: 'POST' });
        status.className = 'error';
        status.textContent = 'Robot arrêté !';
    } catch (err) {
        status.className = 'error';
        status.textContent = "Erreur lors de l'arrêt du robot";
    }
});

document.getElementById('btn-resume').addEventListener('click', async () => {
    // Demande une reprise de la trajectoire depuis le dernier index connu.
    status.className = 'sending';
    status.textContent = 'Reprise de la trajectoire...';
    try {
        const response = await fetch('/resume_robot', { method: 'POST' });
        const data = await response.json();
        
        if (data.status === 'ok') {
            status.className = 'success';
            status.textContent = 'Trajectoire reprise !';

            // Relancement automatique du monitoring du statut après reprise
            startStatusMonitoring();
        } else {
            status.className = 'error';
            status.textContent = 'Aucune trajectoire à reprendre';
        }
    } catch (err) {
        status.className = 'error';
        status.textContent = 'Erreur de connexion lors de la reprise';
    }
});