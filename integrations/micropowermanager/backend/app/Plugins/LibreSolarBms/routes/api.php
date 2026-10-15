<?php

use App\Plugins\LibreSolarBms\Http\Controllers\LibreSolarBmsBatteryController;
use App\Plugins\LibreSolarBms\Http\Controllers\LibreSolarBmsIngestController;
use Illuminate\Support\Facades\Route;

Route::prefix('libre-solar-bms')->group(function () {
    // Called by the mpm-ingestor service. The company is resolved from the API key
    // (see ThirdPartyApiResolverService::LIBRE_SOLAR_BMS_INGEST_API).
    Route::prefix('ingest')->middleware('auth:api-key')->group(function () {
        Route::post('/summary', [LibreSolarBmsIngestController::class, 'summary']);
        Route::post('/events', [LibreSolarBmsIngestController::class, 'events']);
    });

    Route::middleware('auth:api')->group(function () {
        Route::get('/batteries', [LibreSolarBmsBatteryController::class, 'index']);
        Route::get('/batteries/{batteryId}', [LibreSolarBmsBatteryController::class, 'show'])->where('batteryId', '[0-9]+');
        Route::get('/batteries/{batteryId}/events', [LibreSolarBmsBatteryController::class, 'events'])->where('batteryId', '[0-9]+');
        Route::get('/batteries/{batteryId}/summaries', [LibreSolarBmsBatteryController::class, 'summaries'])->where('batteryId', '[0-9]+');
        Route::get('/batteries/{batteryId}/trend', [LibreSolarBmsBatteryController::class, 'trend'])->where('batteryId', '[0-9]+');
    });
});
