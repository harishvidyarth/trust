(function () {
  var C = (window.C = window.C || {});
  C.state = { demo: false, me: null, csrf: null, demoReason: "" };

  function ApiError(status, message, network) {
    this.name = "ApiError";
    this.status = status;
    this.message = message;
    this.network = Boolean(network);
  }
  ApiError.prototype = Object.create(Error.prototype);
  C.ApiError = ApiError;

  C.base = function () {
    return String(window.TRUST_API || "http://localhost:8000").replace(/\/+$/, "");
  };

  function detailOf(body, status) {
    if (body && typeof body.detail === "string") return body.detail;
    if (body && Array.isArray(body.detail) && body.detail.length) {
      return body.detail
        .map(function (item) {
          return item && item.msg ? item.msg : String(item);
        })
        .join("; ");
    }
    return "Request failed (" + status + ")";
  }

  C.api = async function (method, path, options) {
    var opts = options || {};
    if (C.state.demo) return C.demo.handle(method, path, opts);
    var headers = {};
    var body;
    if (opts.form) {
      body = opts.form;
    } else if (opts.json !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(opts.json);
    }
    if (method !== "GET" && C.state.csrf) headers["X-CSRF-Token"] = C.state.csrf;
    var response;
    try {
      response = await fetch(C.base() + path, { method: method, headers: headers, body: body, credentials: "include" });
    } catch (error) {
      throw new ApiError(0, "The service is not reachable at " + C.base() + ".", true);
    }
    var data = null;
    try {
      data = await response.json();
    } catch (error) {
      data = null;
    }
    if (!response.ok) throw new ApiError(response.status, detailOf(data, response.status), false);
    return data;
  };

  C.friendly = function (error) {
    if (!error) return "Something went wrong.";
    if (error.status === 401) return "Your session has ended. Please sign in again.";
    if (error.status === 403) return "Your account does not have permission to do that.";
    if (error.status === 404) return "That item could not be found.";
    if (error.status === 429) return "Too many requests. Wait a moment and try again.";
    return error.message || "Something went wrong.";
  };
})();
