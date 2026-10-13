<?php

namespace App\Plugins\LibreSolarBms\Http\Controllers;

use App\Http\Controllers\Controller;
use App\Plugins\LibreSolarBms\Http\Requests\IngestEventsRequest;
use App\Plugins\LibreSolarBms\Http\Requests\IngestSummaryRequest;
use App\Plugins\LibreSolarBms\Services\LibreSolarBmsService;
use Dedoc\Scramble\Attributes\Group;
use Illuminate\Http\JsonResponse;

#[Group('Plugins / Libre Solar BMS', 'Receives low-frequency battery health from the BMS gateway (API key auth)')]
class LibreSolarBmsIngestController extends Controller {
    public function __construct(
        private LibreSolarBmsService $service,
    ) {}

    /**
     * Store the latest health summary of one battery. Older summaries than the stored one are ignored.
     */
    public function summary(IngestSummaryRequest $request): JsonResponse {
        $battery = $this->service->recordSummary($request->validated());

        return response()->json(['battery_id' => $battery->id]);
    }

    /**
     * Store fault and connectivity events. Re-sending the same event is harmless.
     */
    public function events(IngestEventsRequest $request): JsonResponse {
        return response()->json($this->service->recordEvents($request->validated()['events']));
    }
}
