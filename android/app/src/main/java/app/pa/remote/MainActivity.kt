package app.pa.remote

import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.graphics.Color
import android.net.wifi.WifiManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.KeyEvent
import android.view.View
import android.view.WindowManager
import android.webkit.JavascriptInterface
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.HttpURLConnection
import java.net.Inet4Address
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.NetworkInterface
import java.net.Socket
import java.net.SocketTimeoutException
import java.net.URL
import java.util.Collections
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

/**
 * Télécommande du logiciel de vidéothèque : trouve tout seul le PC sur le réseau
 * (box, partage de connexion du téléphone ou câble), puis affiche sa page de télécommande
 * en plein écran. Les boutons de volume du téléphone règlent le volume.
 */
class MainActivity : Activity() {

    private lateinit var web: WebView
    private lateinit var overlay: LinearLayout
    private lateinit var message: TextView
    private lateinit var progress: ProgressBar
    private val ui = Handler(Looper.getMainLooper())
    private val prefs by lazy { getSharedPreferences("remote", Context.MODE_PRIVATE) }
    private var searching = false
    private var lost = 0

    /** Nouvel essai automatique tant que le PC n'est pas trouvé (plus de page bloquée). */
    private val autoRetry = Runnable { if (overlay.visibility == View.VISIBLE && !searching) discover(auto = true) }

    /** Surveillance : si le PC ne répond plus (logiciel fermé, relancé, adresse changée), on le recherche. */
    private val watchdog = object : Runnable {
        override fun run() {
            val url = prefs.getString("url", null)
            if (url != null && overlay.visibility != View.VISIBLE && !searching) {
                thread {
                    val ok = reachable(url)
                    ui.post {
                        lost = if (ok) 0 else lost + 1
                        if (lost >= 2) {
                            lost = 0
                            showOverlay("Connexion au PC perdue…\nNouvelle recherche.", true)
                            discover(auto = true)
                        }
                    }
                }
            }
            ui.postDelayed(this, 5000)
        }
    }

    data class Pc(val name: String, val host: String, val port: Int) {
        fun url() = "http://$host:$port/"
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        window.statusBarColor = Color.parseColor("#0F0F12")
        window.navigationBarColor = Color.parseColor("#0F0F12")

        val root = FrameLayout(this)
        root.setBackgroundColor(Color.parseColor("#0F0F12"))
        web = WebView(this)
        web.setBackgroundColor(Color.parseColor("#0F0F12"))
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        web.settings.mediaPlaybackRequiresUserGesture = false
        web.addJavascriptInterface(Bridge(), "RemoteApp")
        web.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView, url: String) {
                hideOverlay()
                syncToken()
            }

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (request.isForMainFrame) {
                    ui.post { showOverlay("PC injoignable…\nNouvelle recherche.", true); discover(auto = true) }
                }
            }

            override fun onReceivedHttpError(view: WebView, request: WebResourceRequest, response: WebResourceResponse) {
                // page d'erreur (« Not Found »…) : ce n'est pas la télécommande, on cherche le bon PC
                if (request.isForMainFrame) {
                    ui.post { showOverlay("Réponse inattendue de ${request.url.host}…\nNouvelle recherche.", true); discover(auto = true) }
                }
            }
        }
        root.addView(web, FrameLayout.LayoutParams(-1, -1))

        overlay = LinearLayout(this)
        overlay.orientation = LinearLayout.VERTICAL
        overlay.gravity = Gravity.CENTER
        overlay.setPadding(48, 48, 48, 48)
        overlay.setBackgroundColor(Color.parseColor("#0F0F12"))
        progress = ProgressBar(this)
        message = TextView(this)
        message.setTextColor(Color.parseColor("#E9E9F0"))
        message.textSize = 17f
        message.gravity = Gravity.CENTER
        message.setPadding(0, 32, 0, 32)
        val retry = button("Chercher à nouveau") { discover(auto = false) }
        val manual = button("Saisir l'adresse du PC") { askAddress() }
        overlay.addView(progress)
        overlay.addView(message)
        overlay.addView(retry)
        overlay.addView(manual)
        root.addView(overlay, FrameLayout.LayoutParams(-1, -1))
        setContentView(root)
        immersive()

        val last = prefs.getString("url", null)
        if (last != null) {
            showOverlay("Connexion au PC…", true)
            thread {
                val ok = reachable(last)
                ui.post { if (ok) web.loadUrl(last) else discover(auto = true) }
            }
        } else {
            discover(auto = true)
        }
        ui.postDelayed(watchdog, 5000)
    }

    private fun button(text: String, action: () -> Unit): Button {
        val b = Button(this)
        b.text = text
        b.isAllCaps = false
        b.setOnClickListener { action() }
        return b
    }

    private fun immersive() {
        @Suppress("DEPRECATION")
        window.decorView.systemUiVisibility = (View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
            or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or View.SYSTEM_UI_FLAG_FULLSCREEN
            or View.SYSTEM_UI_FLAG_LAYOUT_STABLE or View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
            or View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN)
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) immersive()
    }

    private fun showOverlay(text: String, busy: Boolean) {
        message.text = text
        progress.visibility = if (busy) View.VISIBLE else View.GONE
        overlay.visibility = View.VISIBLE
    }

    private fun hideOverlay() {
        overlay.visibility = View.GONE
    }

    // ---------------------------------------------------------------- recherche du PC
    private fun discover(auto: Boolean) {
        if (searching) return
        searching = true
        showOverlay("Recherche du PC sur le réseau…", true)
        thread {
            var found = scan()
            if (found.isEmpty()) {
                ui.post { showOverlay("Recherche approfondie du PC…\n(partage de connexion : quelques secondes)", true) }
                found = sweep()
            }
            ui.post {
                searching = false
                when {
                    found.isEmpty() -> {
                        showOverlay(
                            "PC introuvable — nouvel essai toutes les 5 s.\n\nSur le PC : ouvre le logiciel et active « Téléphone ». " +
                                "Le téléphone et le PC doivent être sur le même réseau " +
                                "(même Wi-Fi, ou le PC connecté au partage de connexion du téléphone).", true)
                        ui.removeCallbacks(autoRetry)
                        ui.postDelayed(autoRetry, 5000)
                    }
                    found.size == 1 -> open(found[0])
                    else -> choose(found)
                }
            }
        }
    }

    private fun choose(list: List<Pc>) {
        val labels = list.map { "${it.name}  (${it.host})" }.toTypedArray()
        AlertDialog.Builder(this)
            .setTitle("Choisis le PC")
            .setItems(labels) { _, i -> open(list[i]) }
            .setOnCancelListener { showOverlay("Aucun PC choisi.", false) }
            .show()
    }

    private fun open(pc: Pc) {
        prefs.edit().putString("url", pc.url()).apply()
        showOverlay("Connexion à ${pc.name}…", true)
        web.loadUrl(pc.url())
    }

    private fun askAddress() {
        val input = EditText(this)
        input.hint = "192.168.1.20:8765"
        input.setText(prefs.getString("url", "")?.removePrefix("http://")?.trimEnd('/') ?: "")
        AlertDialog.Builder(this)
            .setTitle("Adresse du PC")
            .setMessage("Elle est affichée dans la fenêtre « Téléphone » du logiciel.")
            .setView(input)
            .setPositiveButton("OK") { _, _ ->
                var a = input.text.toString().trim().removePrefix("http://").trimEnd('/')
                if (a.isNotEmpty()) {
                    if (!a.contains(":")) a += ":8765"
                    val host = a.substringBefore(":")
                    val port = a.substringAfter(":").toIntOrNull() ?: 8765
                    open(Pc(host, host, port))
                }
            }
            .setNegativeButton("Annuler", null)
            .show()
    }

    /** Question UDP « es-tu là ? » à tout le réseau local (et à chaque adresse du sous-réseau). */
    private fun scan(): List<Pc> {
        val found = LinkedHashMap<String, Pc>()
        var lock: WifiManager.MulticastLock? = null
        try {
            val wifi = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
            lock = wifi.createMulticastLock("remote").also { it.setReferenceCounted(false); it.acquire() }
        } catch (_: Exception) {
        }
        try {
            DatagramSocket().use { sock ->
                sock.broadcast = true
                sock.soTimeout = 300
                val questions = listOf("FOCAL?", "PHOQUE AL?").map { it.toByteArray() }
                val targets = LinkedHashSet<InetAddress>()
                targets.add(InetAddress.getByName("255.255.255.255"))
                for (ni in NetworkInterface.getNetworkInterfaces()) {
                    if (!ni.isUp || ni.isLoopback) continue
                    for (ia in ni.interfaceAddresses) {
                        val a = ia.address as? Inet4Address ?: continue
                        ia.broadcast?.let { targets.add(it) }
                        // partage de connexion : le téléphone est le routeur, on interroge chaque voisin
                        if (a.isSiteLocalAddress && ia.networkPrefixLength >= 22) {
                            val b = a.address
                            for (i in 1..254) {
                                if (i == (b[3].toInt() and 0xFF)) continue
                                targets.add(InetAddress.getByAddress(byteArrayOf(b[0], b[1], b[2], i.toByte())))
                            }
                        }
                    }
                }
                repeat(2) {
                    for (t in targets) for (q in questions) {
                        try {
                            sock.send(DatagramPacket(q, q.size, t, 8767))
                        } catch (_: Exception) {
                        }
                    }
                    val end = System.currentTimeMillis() + 1300
                    val buf = ByteArray(2048)
                    while (System.currentTimeMillis() < end) {
                        try {
                            val p = DatagramPacket(buf, buf.size)
                            sock.receive(p)
                            val j = JSONObject(String(p.data, 0, p.length, Charsets.UTF_8))
                            val host = p.address.hostAddress ?: continue
                            val port = j.optInt("port", 8765)
                            found["$host:$port"] = Pc(j.optString("name", host), host, port)
                        } catch (_: SocketTimeoutException) {
                        } catch (_: Exception) {
                        }
                    }
                    if (found.isNotEmpty()) return found.values.toList()
                }
            }
        } catch (_: Exception) {
        } finally {
            try {
                lock?.release()
            } catch (_: Exception) {
            }
        }
        return found.values.toList()
    }

    /**
     * Recherche directe : essaie le port de la télécommande sur chaque adresse des réseaux du téléphone.
     * Indispensable quand le téléphone fait lui-même le partage de connexion : Android y envoie mal les
     * messages « es-tu là ? », alors qu'une connexion directe passe très bien.
     */
    private fun sweep(): List<Pc> {
        val ports = LinkedHashSet<Int>()
        val prefixes = LinkedHashSet<String>()
        val last = prefs.getString("url", "")?.removePrefix("http://")?.trimEnd('/') ?: ""
        if (last.contains(".")) {
            val host = last.substringBefore(":")
            last.substringAfter(":", "").toIntOrNull()?.let { ports.add(it) }
            if (host.count { it == '.' } == 3) prefixes.add(host.substringBeforeLast("."))
        }
        ports.add(8765)
        try {
            for (ni in NetworkInterface.getNetworkInterfaces()) {
                if (!ni.isUp || ni.isLoopback) continue
                for (ia in ni.interfaceAddresses) {
                    val a = ia.address as? Inet4Address ?: continue
                    if (a.isSiteLocalAddress) prefixes.add(a.hostAddress!!.substringBeforeLast("."))
                }
            }
        } catch (_: Exception) {
        }
        prefixes.add("192.168.43")                 // partage de connexion Android classique
        val own = HashSet<String>()
        try {
            for (ni in NetworkInterface.getNetworkInterfaces()) for (ia in ni.interfaceAddresses)
                (ia.address as? Inet4Address)?.hostAddress?.let { own.add(it) }
        } catch (_: Exception) {
        }
        val found = Collections.synchronizedList(ArrayList<Pc>())
        for (prefix in prefixes) {
            val pool = Executors.newFixedThreadPool(48)
            for (i in 1..254) for (port in ports) {
                val host = "$prefix.$i"
                if (host in own) continue
                pool.execute {
                    try {
                        Socket().use { it.connect(InetSocketAddress(host, port), 450) }
                        if (reachable("http://$host:$port/")) found.add(Pc(host, host, port))
                    } catch (_: Exception) {
                    }
                }
            }
            pool.shutdown()
            pool.awaitTermination(15, TimeUnit.SECONDS)
            if (found.isNotEmpty()) break
        }
        return found.distinctBy { "${it.host}:${it.port}" }
    }

    /** Vrai seulement si c'est bien le logiciel qui répond (pas un autre appareil sur la même adresse). */
    private fun reachable(url: String): Boolean = try {
        val c = URL(url + "manifest.json").openConnection() as HttpURLConnection
        c.connectTimeout = 1500
        c.readTimeout = 1500
        val ok = c.responseCode == 200 && c.inputStream.bufferedReader().use { it.readText() }.contains("start_url")
        c.disconnect()
        ok
    } catch (_: Exception) {
        false
    }

    // ---------------------------------------------------------------- code d'accès gardé d'un PC à l'autre
    private fun syncToken() {
        val saved = prefs.getString("token", null)
        val js = "(function(){try{var t=localStorage.getItem('focal_t');" +
            "if(t){RemoteApp.saveToken(t);return 'ok';}" +
            (if (saved != null) "localStorage.setItem('focal_t','" + saved.replace("'", "") + "');return 'reload';" else "") +
            "}catch(e){}return '';})()"
        web.evaluateJavascript(js) { r ->
            if (r != null && r.contains("reload")) web.reload()
        }
    }

    inner class Bridge {
        @JavascriptInterface
        fun saveToken(t: String) {
            prefs.edit().putString("token", t).apply()
        }
    }

    // ---------------------------------------------------------------- boutons du téléphone
    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        val d = when (keyCode) {
            KeyEvent.KEYCODE_VOLUME_UP -> 5
            KeyEvent.KEYCODE_VOLUME_DOWN -> -5
            else -> 0
        }
        if (d != 0 && overlay.visibility != View.VISIBLE) {
            web.evaluateJavascript(
                "(function(){var e=document.getElementById('oVol');if(!e||!e.offsetParent){" +
                    "var l=document.querySelectorAll('input[type=range]');e=null;for(var i=0;i<l.length;i++){" +
                    "if(l[i].offsetParent&&/vol/i.test(l[i].id||l[i].className||'')){e=l[i];break;}}}" +
                    "if(!e)return;e.value=Math.max(0,Math.min(100,(+e.value)+($d)));" +
                    "e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));" +
                    "if(navigator.vibrate)navigator.vibrate(15);})()", null)
            return true
        }
        if (keyCode == KeyEvent.KEYCODE_BACK && web.canGoBack()) {
            web.goBack()
            return true
        }
        return super.onKeyDown(keyCode, event)
    }

    override fun onKeyUp(keyCode: Int, event: KeyEvent): Boolean {
        if (keyCode == KeyEvent.KEYCODE_VOLUME_UP || keyCode == KeyEvent.KEYCODE_VOLUME_DOWN) return true
        return super.onKeyUp(keyCode, event)
    }
}
