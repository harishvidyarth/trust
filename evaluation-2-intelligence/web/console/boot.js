(function () {
  var C = window.C;
  var params = new URLSearchParams(location.search);
  C.params = { sample: params.get("sample"), step: params.get("step"), select: params.get("select"), tab: params.get("tab") };

  C.$("helpBtn").addEventListener("click", C.showHelp);
  C.$("dialog").addEventListener("click", function (event) {
    if (event.target === C.$("dialog")) C.$("dialog").close();
  });

  async function start() {
    C.syncThemeButton();
    if (params.get("demo") === "1") {
      var reachable = true;
      try {
        await C.api("GET", "/v1/auth/me");
      } catch (probe) {
        reachable = !probe.network;
      }
      if (reachable) {
        var clean = new URL(location.href);
        clean.searchParams.delete("demo");
        clean.searchParams.delete("as");
        history.replaceState(null, "", clean.toString());
        params = new URLSearchParams(clean.search);
      } else {
        C.enterDemo("");
      }
    }
    var role = params.get("as");
    if (C.state.demo && role) {
      if (await C.login(role, C.demo.password)) return;
    }
    if (C.state.demo) {
      C.renderShell();
      C.showLogin("");
      return;
    }
    try {
      var me = await C.api("GET", "/v1/auth/me");
      C.state.me = { username: me.username, role: me.role };
      C.state.csrf = me.csrf_token || me.csrf || null;
      C.renderShell();
      C.go(C.homeFor(me.role));
    } catch (error) {
      C.renderShell();
      if (error.network) {
        C.state.needsCert = /^https:/i.test(C.base());
        C.enterDemo(C.state.needsCert
          ? "Your browser has not allowed the secure connection to the service yet, so this is sample data."
          : "The service at " + C.base() + " could not be reached, so this is sample data.");
        C.showLogin("");
        C.status("Service not reachable. Showing demo data.");
      } else {
        C.showLogin(error.status === 401 ? "" : C.friendly(error));
      }
    }
  }
  start();
})();
