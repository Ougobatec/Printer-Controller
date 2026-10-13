from printer_controller.threads import UiDispatcher, run_in_background


def test_run_in_background_reports_result_and_errors():
    dispatcher, out = UiDispatcher(), []
    run_in_background(dispatcher, lambda: 21 * 2, on_done=out.append).join()
    run_in_background(dispatcher, lambda: 1 / 0, on_error=lambda e: out.append(type(e).__name__)).join()
    dispatcher.drain()
    assert out == [42, "ZeroDivisionError"]
