<?php
// config.php

$BUS_API_KEY = "KfRiwhzgjPeFG9rviJvkpCjnr"; // your RTS key
$RTPIDATAFEED = "bustime";                   // default feed
$BASE_API = "https://riderts.app/bustime/api/v3";

function call_bustime($endpoint, $extra_params = []) {
    global $BUS_API_KEY, $RTPIDATAFEED, $BASE_API;

    $params = array_merge([
        "key" => $BUS_API_KEY,
        "rtpidatafeed" => $RTPIDATAFEED,
        "format" => "json"
    ], $extra_params);

    $url = $BASE_API . "/" . $endpoint . "?" . http_build_query($params);

    // Call Clever BusTime API
    $ch = curl_init($url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_TIMEOUT, 10);
    // debug / compatibility for shared hosting SSL issues
    curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, false);
    curl_setopt($ch, CURLOPT_SSL_VERIFYHOST, 0);

       $response = curl_exec($ch);

    if ($response === false) {
        $curl_error = curl_error($ch);
        $curl_errno = curl_errno($ch);

        http_response_code(500);
        echo json_encode([
            "error" => "Failed to reach upstream API",
            "curl_errno" => $curl_errno,
            "curl_error" => $curl_error,
            "url" => $url
        ]);
        curl_close($ch);
        exit;
    }

    curl_close($ch);

    $json = json_decode($response, true);
    // BusTime wraps the real data in "bustime-response"
    if (isset($json["bustime-response"])) {
        return $json["bustime-response"];
    }
    return [];
}

// Tell browser we are returning JSON
header("Content-Type: application/json");
header("Access-Control-Allow-Origin: *"); // allow frontend JS
?>