// Matches engine replies to the calls waiting for them, by request id. The engine answers quick
// calls while a long one runs, so replies can come back in any order; a reply goes only to the
// call that sent its id, and one nobody waits for (its call already timed out) is dropped.

use std::collections::HashMap;
use std::sync::mpsc::{channel, Receiver, Sender};

use serde_json::Value;

pub struct Pending<T> {
    waiting: HashMap<u64, Sender<T>>,
    closed: bool,
}

impl<T> Default for Pending<T> {
    fn default() -> Self {
        Self { waiting: HashMap::new(), closed: false }
    }
}

impl<T> Pending<T> {
    /// Waits for the reply to `id`; None once the engine's output has closed.
    pub fn register(&mut self, id: u64) -> Option<Receiver<T>> {
        if self.closed {
            return None;
        }
        let (send, receive) = channel();
        self.waiting.insert(id, send);
        Some(receive)
    }

    /// Hands `reply` to the call waiting for `id`. False when no call is waiting for it.
    pub fn deliver(&mut self, id: Option<u64>, reply: T) -> bool {
        match id.and_then(|id| self.waiting.remove(&id)) {
            Some(waiter) => waiter.send(reply).is_ok(),
            None => false,
        }
    }

    /// The call stopped waiting (timed out or could not send): a late reply is dropped.
    pub fn forget(&mut self, id: u64) {
        self.waiting.remove(&id);
    }

    /// The engine's output closed: every waiting call, and every later one, hears so.
    pub fn close(&mut self) {
        self.closed = true;
        self.waiting.clear();
    }
}

/// The request id a reply line answers, if it has one.
pub fn reply_id(line: &str) -> Option<u64> {
    serde_json::from_str::<Value>(line).ok()?.get("id")?.as_u64()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::mpsc::TryRecvError;
    use std::sync::{Arc, Mutex};
    use std::thread;

    #[test]
    fn reads_the_id_of_a_reply() {
        assert_eq!(reply_id(r#"{"id": 7, "result": 1}"#), Some(7));
        assert_eq!(reply_id(r#"{"id": null, "error": "invalid JSON"}"#), None);
        assert_eq!(reply_id(r#"{"id": "7"}"#), None);
        assert_eq!(reply_id("not json"), None);
    }

    #[test]
    fn replies_out_of_order_reach_their_own_callers() {
        let mut pending = Pending::default();
        let first = pending.register(1).unwrap();
        let second = pending.register(2).unwrap();
        assert!(pending.deliver(Some(2), "two"));
        assert_eq!(first.try_recv(), Err(TryRecvError::Empty));
        assert!(pending.deliver(Some(1), "one"));
        assert_eq!(first.recv().unwrap(), "one");
        assert_eq!(second.recv().unwrap(), "two");
    }

    #[test]
    fn a_reply_with_another_id_is_never_handed_to_a_waiting_call() {
        let mut pending = Pending::default();
        let waiting = pending.register(5).unwrap();
        assert!(!pending.deliver(Some(4), "stale"));
        assert!(!pending.deliver(None, "no id"));
        assert_eq!(waiting.try_recv(), Err(TryRecvError::Empty));
        assert!(pending.deliver(Some(5), "mine"));
        assert_eq!(waiting.recv().unwrap(), "mine");
    }

    #[test]
    fn a_late_reply_after_a_timeout_is_dropped() {
        let mut pending = Pending::default();
        let timed_out = pending.register(1).unwrap();
        pending.forget(1);
        drop(timed_out);
        let next = pending.register(2).unwrap();
        assert!(!pending.deliver(Some(1), "late"));
        assert_eq!(next.try_recv(), Err(TryRecvError::Empty));
    }

    #[test]
    fn each_reply_is_delivered_once() {
        let mut pending = Pending::default();
        let waiting = pending.register(3).unwrap();
        assert!(pending.deliver(Some(3), "first"));
        assert!(!pending.deliver(Some(3), "again"));
        assert_eq!(waiting.recv().unwrap(), "first");
        assert!(waiting.try_recv().is_err());
    }

    #[test]
    fn closing_wakes_every_waiting_call_and_refuses_new_ones() {
        let mut pending: Pending<&str> = Pending::default();
        let a = pending.register(1).unwrap();
        let b = pending.register(2).unwrap();
        pending.close();
        assert!(a.recv().is_err());
        assert!(b.recv().is_err());
        assert!(pending.register(3).is_none());
    }

    #[test]
    fn concurrent_callers_each_get_their_own_reply() {
        let pending = Arc::new(Mutex::new(Pending::default()));
        let receivers: Vec<_> = (1..=50u64).map(|id| (id, pending.lock().unwrap().register(id).unwrap())).collect();
        let callers: Vec<_> = receivers
            .into_iter()
            .map(|(id, reply)| thread::spawn(move || (id, reply.recv().unwrap())))
            .collect();
        // The engine answers in reverse order.
        for id in (1..=50u64).rev() {
            assert!(pending.lock().unwrap().deliver(Some(id), format!("reply {id}")));
        }
        for caller in callers {
            let (id, reply) = caller.join().unwrap();
            assert_eq!(reply, format!("reply {id}"));
        }
    }
}
