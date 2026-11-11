#![forbid(unsafe_code)]

//! Records the SHELL balance this account sees (`probe.record`) or refuses
//! the call (`probe.refuse`). `repro.sh` uses the recorded balances to
//! measure what an external message costs the destination account.
//!
//! Data: observed SHELL balance (u128 LE, 16 bytes) | owner key (32 bytes).

use avm_contract_sdk::set_owner_key;
use avm_contract_sdk::Caller;
use avm_contract_sdk::CanonicalMessage;
use avm_contract_sdk::Contract;
use avm_contract_sdk::MessageInput;
use avm_contract_sdk::MessageOutput;
use avm_contract_sdk::StateUpdate;
use avm_contract_sdk::OWNER_KEY_BYTES;

pub mod generated {
    include!(env!("AVM_CONTRACT_BINDINGS"));
}

const SHELL_CURRENCY_ID: u32 = 2;
// Matches `owner_key_offset` in avm-contract.toml.
const OWNER_KEY_OFFSET: usize = 16;
const DATA_BYTES: usize = OWNER_KEY_OFFSET + OWNER_KEY_BYTES;

struct ExternalFeeProbe;

impl Contract for ExternalFeeProbe {
    fn init(input: MessageInput<'_>) -> MessageOutput {
        // SDK convention: the deploy's owner_id becomes the owner key.
        let Ok(deploy) = input.deploy() else {
            return MessageOutput::failure(generated::errors::INVALID_CALL);
        };
        let mut data = vec![0; DATA_BYTES];
        if set_owner_key(&mut data, OWNER_KEY_OFFSET as u32, deploy.owner_id()).is_err() {
            return MessageOutput::failure(generated::errors::INVALID_CALL);
        }
        MessageOutput::success(StateUpdate::FullReplace(data))
    }

    fn process_message(input: MessageInput<'_>) -> MessageOutput {
        if !matches!(
            (input.caller, input.message),
            (Caller::External(_), CanonicalMessage::External(_))
        ) || input.state.len() != DATA_BYTES
        {
            return MessageOutput::failure(generated::errors::INVALID_CALL);
        }
        match input.message.method_id() {
            // The balance as this execution sees it: after this message's
            // own reserve was taken. The owner key slot is kept.
            generated::methods::PROBE_RECORD => {
                let shell = input
                    .extra
                    .into_iter()
                    .find(|entry| entry.currency_id == SHELL_CURRENCY_ID)
                    .map_or(0, |entry| entry.amount);
                let mut data = shell.to_le_bytes().to_vec();
                data.extend_from_slice(&input.state[OWNER_KEY_OFFSET..]);
                MessageOutput::success(StateUpdate::FullReplace(data))
            }
            generated::methods::PROBE_REFUSE => MessageOutput::failure(generated::errors::REFUSED),
            _ => MessageOutput::failure(generated::errors::INVALID_CALL),
        }
    }
}

avm_contract_sdk::export_message_contract!(ExternalFeeProbe);
