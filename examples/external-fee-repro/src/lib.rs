#![forbid(unsafe_code)]

//! Records the SHELL balance this account sees (`probe.record`) or refuses
//! the call (`probe.refuse`). `repro.sh` uses the recorded balances to
//! measure what an external message costs the destination account.

use avm_contract_sdk::Caller;
use avm_contract_sdk::CanonicalMessage;
use avm_contract_sdk::Contract;
use avm_contract_sdk::MessageInput;
use avm_contract_sdk::MessageOutput;
use avm_contract_sdk::StateUpdate;

pub mod generated {
    include!(env!("AVM_CONTRACT_BINDINGS"));
}

const SHELL_CURRENCY_ID: u32 = 2;

struct ExternalFeeProbe;

impl Contract for ExternalFeeProbe {
    fn init(_input: MessageInput<'_>) -> MessageOutput {
        MessageOutput::success(StateUpdate::FullReplace(vec![0; 16]))
    }

    fn process_message(input: MessageInput<'_>) -> MessageOutput {
        if !matches!(
            (input.caller, input.message),
            (Caller::External(_), CanonicalMessage::External(_))
        ) {
            return MessageOutput::failure(generated::errors::INVALID_CALL);
        }
        match input.message.method_id() {
            // The balance as this execution sees it: after this message's
            // own reserve was taken.
            generated::methods::PROBE_RECORD => {
                let shell = input
                    .extra
                    .into_iter()
                    .find(|entry| entry.currency_id == SHELL_CURRENCY_ID)
                    .map_or(0, |entry| entry.amount);
                MessageOutput::success(StateUpdate::FullReplace(shell.to_le_bytes().to_vec()))
            }
            generated::methods::PROBE_REFUSE => MessageOutput::failure(generated::errors::REFUSED),
            _ => MessageOutput::failure(generated::errors::INVALID_CALL),
        }
    }
}

avm_contract_sdk::export_message_contract!(ExternalFeeProbe);
